# -*- coding: utf-8 -*-

import asyncio
import subprocess
from collections.abc import (
    AsyncIterator,
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
from agent.adapters.turns.review import ReviewCommandExecutor
from agent.application.turns.commands import TurnApplication
from agent.application.turns.reviews import (
    create_review_command,
    run_review_turn,
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
from frontends.tui.core.models import (
    MenuRequest,
    ViewIdentity,
)
from frontends.tui.features.review import (
    ReviewMenuController,
    ReviewMenuResult,
)
from infrastructure.platform.git_review import WorkspaceReviewGitService
from protocol.client import chat
from protocol.client import review as review_client
from protocol.client.review import ReviewSubmission
from protocol.schema.json_value import JsonObject
from protocol.schema.review import (
    ClientReviewWorkspace,
    MindReviewReceipt,
    ReviewBaseBranchTarget,
    ReviewCommitTarget,
    ReviewCustomTarget,
    ReviewOutput,
    ReviewSession,
    ReviewTarget,
    ReviewUncommittedTarget,
)
from protocol.schema.stream_events import (
    ReviewCancelledEvent,
    ReviewStartedEvent,
    StreamEvent,
    TurnCompletedEvent,
)

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


class _InterruptibleReviewStream:
    """在 Review 启动后等待测试触发远端中断。"""

    def __init__(self, events: tuple[StreamEvent, ...]) -> None:
        self._events = events
        self.started = asyncio.Event()
        self.release_terminal = asyncio.Event()
        self.end_reason: str | None = None
        self.last_event_seq = 0
        self.recovery_probe_count = 0
        self.closed = False

    def __aiter__(self) -> AsyncIterator[StreamEvent]:
        """返回可等待中断回执的事件迭代器。"""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[StreamEvent]:
        """先交付开始事件，再按服务端顺序交付两个终态事件。"""
        first, *terminal = self._events
        self.last_event_seq = first.event_seq or 0
        yield first
        self.started.set()
        await self.release_terminal.wait()
        for event in terminal:
            self.last_event_seq = event.event_seq or self.last_event_seq
            yield event
        self.end_reason = "settled"

    def request_recovery_probe(self) -> None:
        """记录中断命令要求立即核对远端状态。"""
        self.recovery_probe_count += 1

    async def aclose(self) -> None:
        """记录观察流已关闭。"""
        self.closed = True


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
    target: ReviewCustomTarget,
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


def _interrupt_events(
    target: ReviewCustomTarget,
    workspace: ClientReviewWorkspace,
) -> tuple[StreamEvent, ...]:
    """构造 Review 中断要求的严格事件顺序。"""
    return (
        ReviewStartedEvent(
            type="review.started",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=1,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="in_progress",
            review_item_id=REVIEW_ITEM_ID,
            status="in_progress",
            target=target,
            workspace_revision=workspace.revision,
            prompt_version="mind-review/1",
        ),
        ReviewCancelledEvent(
            type="review.cancelled",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=2,
            item_id=REVIEW_ITEM_ID,
            item_kind="review",
            item_status="cancelled",
            review_item_id=REVIEW_ITEM_ID,
            status="cancelled",
            reason="interrupted",
        ),
        TurnCompletedEvent(
            type="turn.completed",
            proto="mind.chat",
            cid=CID,
            sid=SID,
            turn_id=TURN_ID,
            event_seq=3,
            status="interrupted",
            last_event_seq=3,
            completed_at=1.0,
        ),
    )


@pytest.mark.runtime_p0
@pytest.mark.anyio
async def test_review_full_chain_reconnects_without_duplicate_projection(
    monkeypatch,
    tmp_path: Path,
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
        llm_conf={"primary": {"model": "test-model"}},
        environment_snapshot={"workspace": {"root": str(tmp_path)}},
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
            raise OSError("connection lost after review.started")
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
        return await run_review_turn(
            request,
            environment_snapshot,
            capability=protocol_client,
            application=sink,
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
        "review.completed",
    ]


@pytest.mark.runtime_p0
@pytest.mark.anyio
async def test_review_interrupt_waits_for_cancelled_then_turn_terminal(
    monkeypatch,
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
        llm_conf={},
        environment_snapshot=None,
    )
    wire_stream = _InterruptibleReviewStream(
        _interrupt_events(target, workspace),
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
        wire_stream.release_terminal.set()
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
    running = asyncio.create_task(run_review_turn(
        command.request,
        None,
        capability=protocol_client,
        application=sink,
        hint="Focus on lifecycle boundaries",
        on_event=lambda event: delivered.append(event.type),
    ))

    await wire_stream.started.wait()
    receipt = await protocol_client.interrupt_turn(
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
    )
    result = await running

    assert receipt.status == "accepted"
    assert interrupt_calls == [(CID, SID, TURN_ID)]
    assert delivered == [
        "review.started",
        "review.cancelled",
        "turn.completed",
    ]
    assert result.status == "interrupted"
    assert [view.type for view in sink.views] == [
        "review.started",
        "review.cancelled",
    ]
    assert wire_stream.recovery_probe_count == 2
    assert wire_stream.closed


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
    frozen: list[ClientReviewWorkspace] = []
    for target in targets:
        frozen.append(await service.freeze(repo, target))
    snapshots = tuple(frozen)

    assert isinstance(targets[0], ReviewBaseBranchTarget)
    assert targets[0].branch == "main"
    assert isinstance(targets[1], ReviewUncommittedTarget)
    assert isinstance(targets[2], ReviewCommitTarget)
    assert targets[2].title == "Add review feature"
    assert targets[2].sha == _git(repo, "rev-parse", "HEAD")
    assert targets[3] == ReviewCustomTarget("Focus on lifecycle boundaries")
    assert "feature" in snapshots[0].patch
    assert "working change" in snapshots[1].patch
    assert "feature" in snapshots[2].patch
    assert "working change" in snapshots[3].patch
    assert all(
        workspace.revision.startswith("sha256:")
        for workspace in snapshots
    )
