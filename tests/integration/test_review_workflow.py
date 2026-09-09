# -*- coding: utf-8 -*-

import asyncio
import subprocess
from collections.abc import (
    AsyncIterator,
    Callable,
    Coroutine,
)
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agent.adapters.protocol.client import (
    MindChatProtocolClient,
    ProtocolEventCursorStore,
)
from agent.adapters.protocol import client as protocol_client_module
from agent.adapters.protocol.review_events import ReviewEventProjector
from agent.adapters.turns.review import ReviewCommandExecutor
from agent.application.turns.commands import TurnApplication
from agent.application.turns.reviews import (
    create_review_command,
    review_wire_tools,
)
from agent.application.turns.run_result import RunResult
from agent.harness.sessions.owner import SessionRuntimeOwner
from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView,
    Viewport,
)
from agent.protocol import ReviewStreamRequest
from agent.protocol.json_value import ThawedJsonValue
from agent.stores import SQLiteRunStore
from frontends.tui.core.menu import TuiMenu
from frontends.tui.core.interrupt import InterruptDisposition
from frontends.tui.core.models import (
    MenuRequest,
    ViewIdentity,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features.review import (
    ReviewMenuController,
    ReviewMenuResult,
)
from frontends.tui.session.turn import execute_tui_model_turn
from frontends.tui.session.turn_input import TuiTurnInputControl
from infrastructure.platform.git_review import WorkspaceReviewGitService
from protocol.client import chat
from protocol.client import review as review_client
from protocol.client.review import ReviewSubmission
from protocol.schema.json_value import JsonObject
from protocol.schema.review import (
    ClientReviewWorkspace,
    MindReviewReceipt,
    MindReviewRequest,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewOutput,
    ReviewSession,
    ReviewTarget,
    ReviewUncommittedTarget,
    parse_mind_review_request,
)
from protocol.schema.stream_events import (
    StreamEvent,
    TurnCompletedEvent,
)
from protocol.transport.reliable import post_json_reliably

CID = "cid_demo_12345678"
SID = "sid_demo_x_abcdef"
TURN_ID = "turn_review_integration"
REVIEW_ITEM_ID = "review_item_integration"


class _Sink(ApplicationSink):
    """记录端到端 Review 产生的应用展示。"""

    def __init__(self) -> None:
        self.views: list[ApplicationView] = []

    @property
    def viewport(self) -> Viewport:
        """返回固定测试视口。"""
        return Viewport(width=80, height=24)

    def emit(self, view: ApplicationView) -> None:
        """记录一项应用展示。"""
        self.views.append(view)


def _review_tools():
    """返回协议集成测试使用的冻结只读工具目录。"""
    catalog = [
        {
            "name": name,
            "description": name,
            "inputSchema": {"type": "object"},
            "meta": {
                "client_builtin": True,
                "domain": "coding",
                "class": "review_read",
                "review_read_only": True,
            },
        }
        for name in ("read_file", "read_repository")
    ]
    return review_wire_tools(catalog)


async def _consume_review_stream(
    client: MindChatProtocolClient,
    request: ReviewStreamRequest,
    sink: _Sink,
    *,
    hint: str,
    delivered: list[str] | None = None,
    on_event: Callable[[StreamEvent], None] | None = None,
) -> RunResult:
    """在协议集成测试中归约 Review 流，不替代生产共享事件泵。"""
    projector = ReviewEventProjector(sink, hint=hint)
    stream = await client.review(request)
    terminal_status = ""
    try:
        async for event in stream:
            if delivered is not None:
                delivered.append(event.type)
            await projector.observe(event, stream.current_item)
            if on_event is not None:
                on_event(event)
            if isinstance(event, TurnCompletedEvent):
                terminal_status = event.status
    finally:
        await stream.aclose()
    return RunResult(
        status=terminal_status or "reconciliation_required",
        assistant_text=projector.assistant_text(""),
    )


class _MenuRuntime:
    """把真实共享菜单绑定到 Review 菜单控制器端口。"""

    def __init__(self) -> None:
        self.menu = TuiMenu(
            invalidate=lambda: None,
            focus_menu=lambda: None,
            focus_input=lambda: None,
            get_width=lambda: 80,
        )
        self.tasks: list[asyncio.Task[None]] = []

    async def select_menu(self, request: MenuRequest) -> ReviewMenuResult:
        """打开根菜单并只返回类型化 Review 目标。"""
        result = await self.menu.request(request)
        if isinstance(result, (
            ReviewUncommittedTarget,
            ReviewBaseBranchTarget,
            ReviewCommitTarget,
            ReviewCustomTarget,
        )):
            return result
        return None

    def push_menu(self, request: MenuRequest) -> None:
        """把子视图压入共享菜单栈。"""
        self.menu.push(request)

    def start_background_task(
        self,
        coroutine: Coroutine[None, None, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """在测试事件循环中托管目录查询。"""
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task

    def active_menu_view_identity(self) -> ViewIdentity | None:
        """返回共享菜单当前视图身份。"""
        return self.menu.active_view_identity()

    def menu_session_is_active(self, session_id: int) -> bool:
        """返回指定菜单会话是否仍活动。"""
        return self.menu.session_is_active(session_id)


def _git(repo: Path, *args: str) -> str:
    """在隔离仓库执行测试准备命令。"""
    result = subprocess.run(
        ("git", *args),
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    """创建同时包含基线、特性提交和工作区改动的仓库。"""
    repo = tmp_path / "review workflow repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Review Integration")
    _git(repo, "config", "user.email", "review-integration@example.com")
    _git(repo, "branch", "-M", "main")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-qm", "Initial commit")
    _git(repo, "checkout", "-qb", "feature/review")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-qm", "Add review feature")
    (repo / "working.txt").write_text("working change\n", encoding="utf-8")
    return repo


async def _choose_target(
    service: WorkspaceReviewGitService,
    repo: Path,
    preset_index: int,
) -> ReviewTarget:
    """通过真实共享菜单选择一种 Review 目标。"""
    runtime = _MenuRuntime()
    controller = ReviewMenuController(
        runtime,
        service,
        workspace=str(repo),
    )
    pending = asyncio.create_task(controller.choose())
    await asyncio.sleep(0)
    runtime.menu._choose_index(preset_index)
    if preset_index in {0, 2}:
        await asyncio.gather(*runtime.tasks)
        runtime.menu._choose_index(0)
    elif preset_index == 3:
        state = runtime.menu.state
        assert state is not None
        runtime.menu._update_query("Focus on lifecycle boundaries")
        assert runtime.menu._submit_text_input(state)
    result = await pending
    assert isinstance(result, (
        ReviewUncommittedTarget,
        ReviewBaseBranchTarget,
        ReviewCommitTarget,
        ReviewCustomTarget,
    ))
    return result


def _review_payloads(
    target: ReviewTarget,
    workspace: ClientReviewWorkspace,
) -> tuple[JsonObject, JsonObject, JsonObject]:
    """构造可经 wire parser 校验的 Review 完整事件序列。"""
    common: JsonObject = {
        "proto": "mind.chat",
        "cid": CID,
        "sid": SID,
        "turn_id": TURN_ID,
        "presentation_epoch": 1,
    }
    started: JsonObject = {
        **common,
        "type": "review.started",
        "event_seq": 1,
        "item_id": REVIEW_ITEM_ID,
        "item_kind": "review",
        "item_status": "in_progress",
        "review_item_id": REVIEW_ITEM_ID,
        "status": "in_progress",
        "target": target.request_payload(),
        "workspace_revision": workspace.revision,
        "prompt_version": "mind-review/1",
    }
    completed: JsonObject = {
        **common,
        "type": "review.completed",
        "event_seq": 2,
        "item_id": REVIEW_ITEM_ID,
        "item_kind": "review",
        "item_status": "completed",
        "review_item_id": REVIEW_ITEM_ID,
        "status": "completed",
        "output": ReviewOutput(
            findings=(),
            overall_correctness="correct",
            overall_explanation="No findings.",
            overall_confidence_score=0.95,
        ).payload(),
    }
    terminal: JsonObject = {
        **common,
        "type": "turn.completed",
        "event_seq": 3,
        "status": "completed",
        "last_event_seq": 3,
        "completed_at": 1.0,
    }
    return started, completed, terminal


@pytest.mark.runtime_p0
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "receipt_status"),
    (
        (ReviewUncommittedTarget(), "accepted"),
        (ReviewBaseBranchTarget("main", "a" * 40), "idempotent"),
        (ReviewCommitTarget("b" * 40, "Fixture commit"), "accepted"),
        (ReviewCustomTarget("Focus on lifecycle boundaries"), "idempotent"),
    ),
)
async def test_controllable_protocol_accepts_all_empty_workspace_targets(
    monkeypatch,
    target: ReviewTarget,
    receipt_status: str,
) -> None:
    """验证四类正式 wire 请求均可登记并完成同一 Review 事件链。"""
    workspace = ClientReviewWorkspace.create()
    command = create_review_command(
        local_session_id="session_review_target_matrix",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=target,
        workspace=workspace,
        pref_config={"primary": {"model": "test-model"}},
        environment_snapshot=None,
        tools=_review_tools(),
    )
    event_payloads = _review_payloads(target, workspace)
    submitted = []

    async def register(
        _url: str,
        *,
        headers: dict[str, str],
        payload: JsonObject,
        timeout: float,
    ) -> httpx.Response:
        """严格解析请求并返回 accepted 或 idempotent 回执。"""
        assert headers == {"authorization": "test"}
        assert timeout == 60.0
        parsed = parse_mind_review_request(payload)
        assert parsed.target == target
        assert parsed.workspace == workspace
        assert parsed.execution.tools
        assert all("meta" not in tool for tool in payload["execution"]["tools"])
        submitted.append(parsed)
        return httpx.Response(
            202,
            json={
                "ok": True,
                "data": {
                    "request_id": command.request.request_id,
                    "status": receipt_status,
                    "cid": CID,
                    "sid": SID,
                    "turn_id": TURN_ID,
                    "review_session": {"cid": CID, "sid": SID},
                    "delivery": "inline",
                },
            },
            request=httpx.Request("POST", "https://example.test/mind-review"),
        )

    async def streaming(
        _url: str,
        _headers: dict[str, str],
        _payload: JsonObject,
        _timeout: float,
    ) -> AsyncIterator[JsonObject]:
        """交付严格 Review Item 和 Turn 终态。"""
        for payload in event_payloads:
            yield payload

    monkeypatch.setattr(review_client, "post_json_reliably", register)
    monkeypatch.setattr(
        review_client,
        "build_service_headers",
        lambda: {"authorization": "test"},
    )
    monkeypatch.setattr(
        review_client.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )
    monkeypatch.setattr(chat, "streaming", streaming)
    monkeypatch.setattr(
        chat,
        "build_service_headers",
        lambda: {"authorization": "test"},
    )
    monkeypatch.setattr(
        chat.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )

    sink = _Sink()
    result = await _consume_review_stream(
        MindChatProtocolClient(),
        command.request,
        sink,
        hint="target matrix",
    )

    assert len(submitted) == 1
    assert result.status == "completed"
    assert result.assistant_text == "No findings."
    assert [view.type for view in sink.views] == [
        "review.started",
        "review.finished",
        "review.completed",
    ]


@pytest.mark.runtime_p0
@pytest.mark.anyio
async def test_review_response_loss_retries_the_same_frozen_identity(
    monkeypatch,
) -> None:
    """验证登记响应丢失后只以相同载荷取得幂等回执。"""
    target = ReviewCustomTarget("Focus on lifecycle boundaries")
    workspace = ClientReviewWorkspace.create()
    command = create_review_command(
        local_session_id="session_review_response_loss",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=target,
        workspace=workspace,
        pref_config={"primary": {"model": "test-model"}},
        environment_snapshot=None,
        tools=_review_tools(),
    )
    attempts: list[JsonObject] = []
    committed_requests: dict[str, MindReviewRequest] = {}

    class ResponseLostClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc_value, _traceback):
            return None

        async def post(
            self,
            url: str,
            *,
            params: dict[str, str] | None,
            headers: dict[str, str],
            json: JsonObject,
        ) -> httpx.Response:
            assert url.endswith("/mind-review")
            assert params is None
            assert headers == {"authorization": "test"}
            payload = dict(json)
            attempts.append(payload)
            parsed = parse_mind_review_request(payload)
            existing = committed_requests.setdefault(parsed.request_id, parsed)
            assert existing == parsed
            if len(attempts) == 1:
                raise httpx.ReadError(
                    "response lost after commit",
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                202,
                json={
                    "ok": True,
                    "data": {
                        "request_id": parsed.request_id,
                        "status": "idempotent",
                        "cid": parsed.cid,
                        "sid": parsed.sid,
                        "turn_id": parsed.turn_id,
                        "review_session": {
                            "cid": parsed.cid,
                            "sid": parsed.sid,
                        },
                        "delivery": "inline",
                    },
                },
                request=httpx.Request("POST", url),
            )

    response_lost_client = ResponseLostClient()

    def client_factory(*, timeout: float) -> ResponseLostClient:
        assert timeout == 60.0
        return response_lost_client

    async def register(
        url: str,
        *,
        headers: dict[str, str],
        payload: JsonObject,
        timeout: float,
    ) -> httpx.Response:
        return await post_json_reliably(
            url,
            headers=headers,
            payload=payload,
            timeout=timeout,
            client_factory=client_factory,
            retry_delays=(0.0, 0.0),
        )

    async def streaming(
        _url: str,
        _headers: dict[str, str],
        _payload: JsonObject,
        _timeout: float,
    ) -> AsyncIterator[JsonObject]:
        for payload in _review_payloads(target, workspace):
            yield payload

    monkeypatch.setattr(review_client, "post_json_reliably", register)
    monkeypatch.setattr(
        review_client,
        "build_service_headers",
        lambda: {"authorization": "test"},
    )
    monkeypatch.setattr(
        review_client.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )
    monkeypatch.setattr(chat, "streaming", streaming)
    monkeypatch.setattr(
        chat,
        "build_service_headers",
        lambda: {"authorization": "test"},
    )
    monkeypatch.setattr(
        chat.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )

    sink = _Sink()
    result = await _consume_review_stream(
        MindChatProtocolClient(),
        command.request,
        sink,
        hint="Focus on lifecycle boundaries",
    )

    assert result.status == "completed"
    assert attempts == [command.request.to_dict(), command.request.to_dict()]
    assert tuple(committed_requests) == (command.request.request_id,)
    assert [view.type for view in sink.views] == [
        "review.started",
        "review.finished",
        "review.completed",
    ]


@pytest.mark.runtime_p0
@pytest.mark.anyio
@pytest.mark.parametrize(
    "disconnect_type",
    (OSError, TimeoutError),
    ids=("connection_lost", "attach_timeout"),
)
async def test_review_full_chain_reconnects_without_duplicate_projection(
    monkeypatch,
    tmp_path: Path,
    disconnect_type: type[OSError],
) -> None:
    """验证持久提交、attach、断线 replay 和唯一终态的完整链路。"""
    target = ReviewCustomTarget("Focus on lifecycle boundaries")
    workspace = ClientReviewWorkspace.create(
        patch="diff --git a/a.py b/a.py\n",
    )
    command = create_review_command(
        local_session_id="session_review_integration",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=target,
        workspace=workspace,
        pref_config={"primary": {"model": "test-model"}},
        environment_snapshot={"workspace": {"root": str(tmp_path)}},
        tools=_review_tools(),
    )
    started, completed, terminal = _review_payloads(target, workspace)
    operations: list[str] = []
    attach_payloads: list[JsonObject] = []

    async def register(
        _url: str,
        *,
        headers: dict[str, str],
        payload: JsonObject,
        timeout: float,
    ) -> httpx.Response:
        """模拟 AppServer 已持久登记并返回 inline 回执。"""
        assert headers == {"authorization": "test"}
        assert payload == command.request.to_dict()
        assert timeout == 60.0
        operations.append("submit")
        return httpx.Response(
            202,
            json={
                "ok": True,
                "data": {
                    "request_id": command.request.request_id,
                    "status": "accepted",
                    "cid": CID,
                    "sid": SID,
                    "turn_id": TURN_ID,
                    "review_session": {"cid": CID, "sid": SID},
                    "delivery": "inline",
                },
            },
            request=httpx.Request("POST", "https://example.test/mind-review"),
        )

    async def streaming(
        url: str,
        _headers: dict[str, str],
        payload: JsonObject,
        _timeout: float,
    ) -> AsyncIterator[JsonObject]:
        """首次 attach 后断线，第二次重放重复项并交付终态。"""
        assert url.endswith("/mind-attach")
        attach_payloads.append(dict(payload))
        operations.append(f"attach:{payload['after_seq']}")
        if len(attach_payloads) == 1:
            yield started
            raise disconnect_type("attach failed after review.started")
        yield started
        yield completed
        yield terminal

    async def status(**_coordinates: str) -> SimpleNamespace:
        """返回重连时冻结的服务端事件水位。"""
        operations.append("status")
        return SimpleNamespace(last_event_seq=3, terminal=None)

    monkeypatch.setattr(review_client, "post_json_reliably", register)
    monkeypatch.setattr(
        review_client,
        "build_service_headers",
        lambda: {"authorization": "test"},
    )
    monkeypatch.setattr(
        review_client.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )
    monkeypatch.setattr(chat, "streaming", streaming)
    monkeypatch.setattr(chat, "get_turn_status", status)
    monkeypatch.setattr(
        chat,
        "build_service_headers",
        lambda: {"authorization": "test"},
    )
    monkeypatch.setattr(
        chat.service_endpoints,
        "endpoint",
        lambda path: f"https://example.test{path}",
    )

    store = SQLiteRunStore(tmp_path / "review-runtime.db")
    application: TurnApplication[RunResult] = TurnApplication(
        store,
        runtime_factory=SessionRuntimeOwner,
    )
    protocol_client = MindChatProtocolClient(ProtocolEventCursorStore())
    sink = _Sink()

    async def execute(
        request: ReviewStreamRequest,
        environment_snapshot: dict[str, ThawedJsonValue] | None,
    ) -> RunResult:
        """把持久 Review Command 接到正式协议能力和展示投影。"""
        _ = environment_snapshot
        return await _consume_review_stream(
            protocol_client,
            request,
            sink,
            hint="Focus on lifecycle boundaries",
        )

    result = await application.submit(
        command,
        ReviewCommandExecutor(execute, request_recorder=application),
    )
    persisted = await store.load_remote_request(command.run_id)
    await application.close()

    assert result.value.status == "completed", result.value.to_dict()
    assert result.value.assistant_text == "No findings."
    assert result.projection.status == "completed"
    assert persisted is not None
    assert persisted.request == command.request
    assert operations == ["submit", "attach:0", "status", "attach:1"]
    assert [payload["after_seq"] for payload in attach_payloads] == [0, 1]
    assert [view.type for view in sink.views] == [
        "review.started",
        "review.finished",
        "review.completed",
    ]


@pytest.mark.runtime_p0
@pytest.mark.anyio
@pytest.mark.parametrize("active_probe_first", (False, True))
async def test_review_interrupt_waits_for_cancelled_then_turn_terminal(
    monkeypatch,
    active_probe_first: bool,
) -> None:
    """验证运行中中断仍按 Review Item 后 Turn 的顺序结算。"""
    target = ReviewCustomTarget("Focus on lifecycle boundaries")
    workspace = ClientReviewWorkspace.create(
        patch="diff --git a/a.py b/a.py\n",
    )
    command = create_review_command(
        local_session_id="session_review_interrupt",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        target=target,
        workspace=workspace,
        pref_config={},
        environment_snapshot=None,
        tools=_review_tools(),
    )
    started = asyncio.Event()
    event_payloads = _review_payloads(target, workspace)
    cancelled = {
        "proto": "mind.chat",
        "cid": CID,
        "sid": SID,
        "turn_id": TURN_ID,
        "presentation_epoch": 1,
        "type": "review.cancelled",
        "event_seq": 2,
        "item_id": REVIEW_ITEM_ID,
        "item_kind": "review",
        "review_item_id": REVIEW_ITEM_ID,
        "status": "cancelled",
        "item_status": "cancelled",
        "reason": "interrupted",
    }
    terminal = {**event_payloads[2], "status": "interrupted"}
    attach_cursors: list[int] = []

    async def streaming(url, _headers, payload, _timeout):
        assert url.endswith("/mind-attach")
        attach_cursors.append(payload["after_seq"])
        if len(attach_cursors) == 1:
            yield event_payloads[0]
            started.set()
            await asyncio.Event().wait()
        else:
            assert payload["after_seq"] == 1
            yield cancelled
            yield terminal

    settled = SimpleNamespace(
        last_event_seq=3,
        terminal=SimpleNamespace(**terminal, duration_ms=1_000),
    )
    status_results = [settled]
    if active_probe_first:
        status_results.insert(0, SimpleNamespace(last_event_seq=1, terminal=None))
    status_probe = AsyncMock(side_effect=status_results)
    monkeypatch.setattr(chat, "streaming", streaming)
    monkeypatch.setattr(chat, "build_service_headers", lambda: {})
    monkeypatch.setattr(
        chat.service_endpoints, "endpoint", lambda path: f"https://example.test{path}",
    )
    monkeypatch.setattr(chat, "get_turn_status", status_probe)
    monkeypatch.setattr(chat, "CONTROL_SETTLEMENT_PROBE_INTERVAL_SEC", 0.01)
    approval_probe = AsyncMock(side_effect=AssertionError(
        "a settled turn must replay without restoring live approvals",
    ))
    monkeypatch.setattr(chat, "reconcile_tool_approval_snapshot", approval_probe)
    wire_stream = chat.observe_turn(
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        on_approval_snapshot=AsyncMock(),
    )
    submission = ReviewSubmission(
        receipt=MindReviewReceipt(
            request_id=command.request.request_id,
            status="accepted",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            review_session=ReviewSession(cid=CID, sid=SID),
            delivery="inline",
        ),
        events=wire_stream,
    )
    monkeypatch.setattr(
        protocol_client_module,
        "_submit_review",
        AsyncMock(return_value=submission),
    )
    interrupt_calls: list[tuple[str, str, str]] = []

    async def interrupt(**coordinates: str | None) -> SimpleNamespace:
        """模拟服务端接受中断后继续交付持久终态。"""
        cid = coordinates.get("cid")
        sid = coordinates.get("sid")
        turn_id = coordinates.get("turn_id")
        assert isinstance(cid, str)
        assert isinstance(sid, str)
        assert isinstance(turn_id, str)
        interrupt_calls.append((cid, sid, turn_id))
        return SimpleNamespace(
            status="accepted",
            request_id="interrupt_review_integration",
            turn_id=TURN_ID,
            client_message_id=None,
        )

    monkeypatch.setattr(protocol_client_module, "_interrupt_turn", interrupt)
    protocol_client = MindChatProtocolClient()
    sink = _Sink()
    delivered: list[str] = []
    runtime = TuiRuntime()
    controller = SimpleNamespace(attach=SimpleNamespace())
    state = SimpleNamespace()
    control = TuiTurnInputControl(
        controller,
        runtime,
        state,
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        protocol_client=protocol_client,
        allow_steer=False,
    )
    running = asyncio.create_task(execute_tui_model_turn(
        sink,
        runtime,
        _consume_review_stream(
            protocol_client,
            command.request,
            sink,
            hint="Focus on lifecycle boundaries",
            delivered=delivered,
            on_event=control.handle_event,
        ),
        turn_input_control=control,
    ))

    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert runtime.submissions.interrupt_input() is (
        InterruptDisposition.CONSUMED
    )
    result = await asyncio.wait_for(running, timeout=1.0)

    assert result is not None
    assert interrupt_calls == [(CID, SID, TURN_ID)]
    assert delivered == [
        "review.started",
        "review.cancelled",
        "turn.completed",
    ]
    assert result.status == "interrupted"
    assert [view.type for view in sink.views] == [
        "review.started",
        "review.finished",
        "review.cancelled",
        "tui.interrupted",
    ]
    assert not runtime.execution_active
    assert not runtime.activity.active
    buffer = runtime.screen.input.buffer
    buffer.text = "next message"
    buffer.cursor_position = len(buffer.text)
    assert runtime.submissions.accept_input(buffer)
    next_submission = await runtime.submissions.read_submission()
    assert next_submission.value == "next message"
    assert attach_cursors == [0, 1]
    assert status_probe.await_count == len(status_results)
    approval_probe.assert_not_awaited()
    assert wire_stream.end_reason == "settled"
    await runtime.close()


@pytest.mark.runtime_p0
@pytest.mark.anyio
async def test_real_git_and_tui_freeze_all_review_targets(tmp_path: Path) -> None:
    """验证四种真实菜单目标均可冻结为规范 Git 快照。"""
    repo = _repository(tmp_path)
    service = WorkspaceReviewGitService()

    selected: list[ReviewTarget] = []
    for preset_index in range(4):
        selected.append(await _choose_target(service, repo, preset_index))
    targets = tuple(selected)
    frozen = []
    for target in targets:
        frozen.append(await service.freeze(repo, target))
    resolved_inputs = tuple(frozen)

    assert isinstance(targets[0], ReviewBaseBranchTarget)
    assert targets[0].branch == "main"
    assert isinstance(targets[1], ReviewUncommittedTarget)
    assert isinstance(targets[2], ReviewCommitTarget)
    assert targets[2].title == "Add review feature"
    assert targets[2].sha == _git(repo, "rev-parse", "HEAD")
    assert targets[3] == ReviewCustomTarget("Focus on lifecycle boundaries")
    assert isinstance(resolved_inputs[0].target, ReviewBaseBranchTarget)
    assert resolved_inputs[0].target.merge_base_sha == _git(
        repo,
        "merge-base",
        "HEAD",
        "main",
    )
    assert resolved_inputs[1].target == targets[1]
    assert resolved_inputs[2].target == targets[2]
    assert resolved_inputs[3].target == targets[3]
    assert all(
        item.workspace == ClientReviewWorkspace.create()
        for item in resolved_inputs
    )
