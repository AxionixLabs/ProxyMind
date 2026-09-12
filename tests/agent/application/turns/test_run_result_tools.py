# -*- coding: utf-8 -*-

"""验证流边界、工具结果投递与不确定回执对账。

这些场景共享工具调用的因果链，继续拆分会隐藏跨事件的不变量。
"""


import asyncio
import dataclasses
import tempfile
import typing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock
)
import pytest
from agent.application.tools.planning import PLAN_STEPS_TOOL
from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.approvals.legacy import DomainApprovalCoordinator
from agent.application.approvals.models import ApprovalOutcome
from frontends.interaction.noninteractive import NonInteractiveInteraction
from agent.adapters.protocol import turn_observation as observation
from agent.adapters.protocol import turn_stream as stream
from agent.application.turns.run_result import RunResult
from agent.ports import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ModelWaitRequested,
    PresentationSuperseded,
    ProtocolCommandError,
    RecoveryChanged,
    RetryChanged,
    ResponseIdentity,
    SourcesOutput,
    ToolCompleted,
    TurnTerminal,
)
from agent.ports import (
    OutputActivityEvent,
    OutputSession,
    OutputSurfaceContext,
)
from agent.application.views import (
    ApprovalReviewView,
    ApprovalView,
    FailureView,
    HookRunView,
)
from infrastructure.mcp import tool_runtime
from infrastructure.mcp.tool_execution import McpToolExecutionAdapter
from infrastructure.platform.hook_command import HookCommandOutput
from agent.ports import ToolRuntimeSources
from agent.application.turns.context import (
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from agent.harness.hooks.runtime import HookRuntime
from agent.application.hooks.context import HookExecutionContext
from agent.application.turns.execution import TurnExecution
from agent.harness.hooks.scope import HookExecutionScope
from agent.stores.approvals.ledger import ApprovalCallLedger
from agent.stores.approvals.facts import InMemoryApprovalFactStore
from agent.stores.approvals.grants import InMemorySessionGrantStore
from agent.application.turns.transcript import build_turn_input_payload
from infrastructure.config.execution_policy_manager import ExecPolicyManager
from agent.domain.execution_policy import (
    Decision,
    Policy,
    PrefixPattern,
    PrefixRule,
)
from agent.harness.tools.client_calls import (
    ClientToolCallOutcome,
    ClientToolCallResult,
)
from agent.ports import ModelCapabilityError
from agent.protocol import ModelStreamRequest
from agent.protocol import TurnObservationRequest
from agent.adapters.protocol.items import CanonicalItemReducer
from agent.composition import open_effect_journal
from agent.harness.tools.plan_execution import PlanExecutionReport
from infrastructure.hooks.discovery import resolve_hook_definitions
from agent.domain.policies import (
    PermissionSettings,
    preset_permissions,
)
from protocol.schema.stream_events import (
    TurnInputAcceptedEvent,
    parse_stream_event as _parse_stream_event,
)
from protocol.client.tools import ToolResultRequestError
from protocol.schema.tool_approval import (
    ToolApprovalSnapshot,
    ToolApprovalSnapshotItem,
)
from protocol.schema.turn_inputs import TurnInput


@pytest.fixture(autouse=True)
def _stream_command_stubs(monkeypatch):
    """为测试替身提供协议客户端方法，不向生产流恢复模块级兼容入口。"""
    monkeypatch.setattr(
        stream,
        "interrupt_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
        raising=False,
    )
    monkeypatch.setattr(
        stream,
        "post_tool_result",
        AsyncMock(return_value={}),
        raising=False,
    )
    monkeypatch.setattr(
        stream,
        "get_tool_result_status",
        AsyncMock(return_value={}),
        raising=False,
    )
    monkeypatch.setattr(
        stream,
        "post_effect_reconciliation",
        AsyncMock(return_value={}),
        raising=False,
    )
    monkeypatch.setattr(
        stream,
        "post_tool_approval",
        AsyncMock(return_value=None),
        raising=False,
    )


def parse_stream_event(payload):
    """为运行流测试补齐当前持久事件 envelope。"""
    current = dict(payload)
    if str(current.get("type") or "") != "ping":
        current.setdefault("proto", "mind.chat")
        current.setdefault("cid", "cid_test")
        current.setdefault("sid", "sid_test")
        current.setdefault("turn_id", "turn_test")
        current.setdefault("event_seq", 1)
        current.setdefault("presentation_epoch", 1)
        if str(current.get("type") or "") == "turn.completed":
            current.setdefault("status", "completed")
            current.setdefault("last_event_seq", current["event_seq"])
            current.setdefault("completed_at", 1.0)
        if str(current.get("type") or "").startswith("text."):
            current.setdefault("segment_id", "segment_test")
        _complete_item_projection(current)
        if str(current.get("type") or "") == "tool.approval_required":
            current.setdefault("approval_id", "approval_test")
            current.setdefault("started_at_ms", 0)
            current.setdefault("status", "pending")
            current.setdefault("ack", None)
            current.setdefault("reason", "")
            current.setdefault(
                "available_decisions",
                ["accept", "acceptForSession", "decline"],
            )
            kind = current.get("kind", "command")
            if kind == "command":
                current.setdefault("environment_id", "workspace-write")
                if isinstance(current.get("command"), str):
                    current["command"] = [current["command"]]
                current.setdefault("command", ["echo", "ready"])
                current.setdefault("cwd", ".")
                current.setdefault("cwd_raw", ".")
                current.setdefault("tty", False)
                current.setdefault("sandbox_permissions", "use_default")
                current.setdefault("additional_permissions", None)
                current.setdefault("proposed_execpolicy_amendment", None)
                current.setdefault("parsed_cmd", [])
            elif kind == "apply_patch":
                current.setdefault("environment_id", "workspace-write")
                current.setdefault("cwd", ".")
                current.setdefault("cwd_raw", ".")
                current.setdefault("files", current.get("patch_scope", ["app.py"]))
                current.pop("patch_scope", None)
                current.setdefault("permissions_preapproved", False)
            elif kind == "network_access":
                current.setdefault("environment_id", "workspace-write")
                current.setdefault("cwd", ".")
                current.setdefault("cwd_raw", ".")
            elif kind == "request_permissions":
                current.setdefault("permissions", {})
            elif kind == "mcp_tool_call":
                current.setdefault("server", "server")
                current.setdefault("tool_name", "tool")
                current.setdefault("arguments", {})
                current.setdefault("mcp_request_id", "mcp_request_test")
    return _parse_stream_event(current)


def _complete_item_projection(payload) -> None:
    """按正式协议为运行流测试补齐 Canonical Item 字段。"""
    event_type = str(payload.get("type") or "")
    projection = None
    if event_type.startswith("text."):
        projection = (
            payload.get("segment_id"),
            "text",
            "in_progress" if event_type == "text.delta" else "completed",
        )
    elif event_type == "tool.call":
        projection = (payload.get("call_id"), "tool_call", "waiting_result")
    elif event_type == "tool.output":
        output_status = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(payload.get("status"), "result_received")
        projection = (
            f"{payload.get('call_id')}:output",
            "tool_output",
            output_status,
        )
    elif event_type.startswith("tool.approval_review."):
        review = payload.get("review")
        review_status = (
            review.get("status")
            if isinstance(review, dict)
            else "in_progress"
        )
        projection = (
            payload.get("review_id") or "review_test",
            "approval",
            {
                "in_progress": "in_progress",
                "approved": "completed",
                "denied": "completed",
                "timed_out": "failed",
                "aborted": "cancelled",
            }.get(review_status, "registered"),
        )
    elif event_type == "tool.approval_required":
        projection = (
            payload.get("approval_id") or "approval_test",
            "approval",
            "waiting_approval",
        )
    elif event_type.startswith("tool.builtin."):
        status = "in_progress" if event_type.endswith("call") else "completed"
        projection = (
            payload.get("builtin_call_id") or "builtin_test",
            "builtin_tool",
            status,
        )
    if projection is None:
        return
    item_id, item_kind, item_status = projection
    payload.setdefault("item_id", item_id)
    payload.setdefault("item_kind", item_kind)
    payload.setdefault("item_status", item_status)


def response_identity(
    *,
    presentation_epoch: int = 1,
    round_no: int = 1,
    attempt: int = 1,
) -> ResponseIdentity:
    """构造流测试使用的稳定响应身份。"""
    return ResponseIdentity("turn_test", presentation_epoch, round_no, attempt)


class _OutputControl(object):
    def __init__(self, *, open_error: Exception | None = None) -> None:
        self.open_error = open_error

    async def open(self) -> None:
        if self.open_error is not None:
            raise self.open_error

    async def stop(self, *, blink: bool = True) -> None:
        _ = blink

    async def record_hidden_output(self, text: str) -> None:
        _ = text

    def record_tool_arguments(self, *_args, **_kwargs) -> None:
        return None


class _Sink(object):
    def __init__(self) -> None:
        self.items: list[object] = []

    async def emit(self, item: object) -> None:
        self.items.append(item)


class _ActivitySink(_Sink):
    """记录 OutputSession 的 typed activity 事实。"""

    def __init__(self) -> None:
        super().__init__()
        self.batches: list[tuple[OutputActivityEvent, ...]] = []

    async def emit_batch(
        self,
        items: tuple[OutputActivityEvent, ...],
    ) -> None:
        self.batches.append(items)
        self.items.extend(items)

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _TranscriptWriter(object):
    def __init__(self, entries) -> None:
        self.entries = entries

    def open(self) -> None:
        return None

    def append(self, event, *, actor=None, payload=None) -> None:
        self.entries.append({
            "event": event,
            "actor": actor,
            "payload": dict(payload or {}),
        })

    def close(self) -> None:
        return None


class _TranscriptStore(object):
    def __init__(self) -> None:
        self.entries = []

    def writer(self, *_args, **_kwargs) -> _TranscriptWriter:
        return _TranscriptWriter(self.entries)


def _output_session(
    context: OutputSurfaceContext,
    *,
    show_hook_lifecycle: bool = False,
    control: _OutputControl | None = None,
) -> OutputSession:
    return OutputSession(
        context=context,
        control=control or _OutputControl(),
        activity=_ActivitySink(),
        content=_Sink(),
        presentation=_Sink(),
        show_hook_lifecycle=show_hook_lifecycle,
    )


def _hook(command, *, matcher=None):
    config = {
        "hooks": [{"type": "command", "command": command}],
    }
    if matcher is not None:
        config["matcher"] = matcher
    return config


def _durable_tool_call(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """把工具场景补齐为当前流式协议 envelope。"""
    current = dict(payload)
    current.update({
        "proto": "mind.chat",
        "presentation_epoch": 1,
    })
    if str(current.get("type") or "") == "tool.approval_required":
        current.setdefault("approval_id", "approval_test")
        current.setdefault("started_at_ms", 0)
        current.setdefault("status", "pending")
        current.setdefault("ack", None)
        current.setdefault("reason", "")
        kind = current.get("kind", "command")
        if kind == "command":
            current.setdefault("environment_id", "workspace-write")
            if isinstance(current.get("command"), str):
                current["command"] = [current["command"]]
            current.setdefault("command", ["echo", "ready"])
            current.setdefault("cwd", ".")
            current.setdefault("cwd_raw", ".")
            current.setdefault("tty", False)
            current.setdefault("sandbox_permissions", "use_default")
            current.setdefault("additional_permissions", None)
            current.setdefault("proposed_execpolicy_amendment", None)
            current.setdefault("parsed_cmd", [])
        elif kind == "apply_patch":
            current.setdefault("environment_id", "workspace-write")
            current.setdefault("cwd", ".")
            current.setdefault("cwd_raw", ".")
            current.setdefault("files", current.get("patch_scope", ["app.py"]))
            current.pop("patch_scope", None)
            current.setdefault("permissions_preapproved", False)
        elif kind == "network_access":
            current.setdefault("environment_id", "workspace-write")
            current.setdefault("cwd", ".")
            current.setdefault("cwd_raw", ".")
        elif kind == "request_permissions":
            current.setdefault("permissions", {})
        elif kind == "mcp_tool_call":
            current.setdefault("server", "server")
            current.setdefault("tool_name", "tool")
            current.setdefault("arguments", {})
            current.setdefault("mcp_request_id", "mcp_request_test")
    return current


def _batched_stream_payloads(
    payload: dict[str, typing.Any],
) -> tuple[dict[str, typing.Any], ...]:
    """把单个工具调用夹具展开为完整的工具批次事件。"""
    if payload.get("type") != "tool.call":
        return (payload,)
    call_id = str(payload.get("call_id") or "")
    batch_id = f"batch_{call_id}"
    boundary = {
        field_name: payload[field_name]
        for field_name in (
            "proto",
            "cid",
            "sid",
            "turn_id",
            "presentation_epoch",
        )
        if field_name in payload
    }
    identity = {
        **boundary,
        "batch_id": batch_id,
        "call_ids": [call_id],
        "count": 1,
    }
    event_seq = payload.get("event_seq")
    start_seq = (
        event_seq - 1
        if isinstance(event_seq, int) and not isinstance(event_seq, bool)
        else None
    )
    done_seq = (
        event_seq + 1
        if isinstance(event_seq, int) and not isinstance(event_seq, bool)
        else None
    )
    return (
        {
            "type": "tool.calls.start",
            **identity,
            "ready": True,
            **({"event_seq": start_seq} if start_seq is not None else {}),
        },
        payload,
        {
            "type": "tool.calls.done",
            **identity,
            **({"event_seq": done_seq} if done_seq is not None else {}),
        },
    )


def _client_result_fields(
    invocation: ToolInvocation,
    *,
    text: str = "done",
) -> dict[str, typing.Any]:
    """构造客户端工具投递使用的严格结果信封。"""
    return {
        "ok": True,
        "tool": invocation.name,
        "source": "client",
        "args": dict(invocation.arguments),
        "text": text,
        "attachments": [],
        "data": {},
    }


def _open_effect_journal(db_path: Path):
    """让流测试使用隔离的本地效果账本。"""
    return open_effect_journal(db_path)


def _host(
    *,
    frontend_active: bool = True,
    effect_journal=None,
) -> SimpleNamespace:
    remembered: list[str] = []
    queued_context: list[tuple[str, ...]] = []

    async def await_cleanup(awaitable) -> None:
        await awaitable

    interaction = SimpleNamespace(
        approval_source="user",
        begin_approval_session=AsyncMock(),
        approval_snapshot_changed=Mock(),
        present_approval=AsyncMock(return_value="accept"),
        end_approval_session=AsyncMock(),
    )
    transcripts = _TranscriptStore()
    execution_policy = ExecPolicyManager(
        workspace_root=Path.cwd(),
        rules_paths=(),
        writable_rules_path=Path.cwd() / ".pytest_cache" / "test-exec-policy.rules",
    )
    effect_directory = None
    if effect_journal is None:
        effect_directory = tempfile.TemporaryDirectory()
        effect_journal = open_effect_journal(
            Path(effect_directory.name) / "effects.db"
        )
    class _SessionContext:
        """实现 TurnSessionContextPort 的测试替身。"""

        @property
        def animate(self) -> bool:
            return True

        @property
        def workspace_root(self) -> str:
            return "."

        @property
        def hook_startup_warnings(self) -> tuple[str, ...]:
            return ()

        @property
        def command_hook_sessions(self):
            return None

        def capture_environment(self, **_kwargs):
            return None

        def skills_payload(self):
            return []

    turn_session_context = _SessionContext()

    class _SessionState:
        """实现 TurnSessionStatePort 的测试替身。"""

        record_context_usage = Mock()
        context_usage_recovery = Mock()
        discard_context_usage_prefix = Mock()

        def queue_turn_context(self, contexts) -> None:
            queued_context.append(tuple(contexts))

        def remember_assistant_reply(self, text: str) -> None:
            remembered.append(text)

    turn_session_state = _SessionState()
    return SimpleNamespace(
        report=SimpleNamespace(output_record_path=""),
        transcripts=transcripts,
        frontend=SimpleNamespace(
            runtime=SimpleNamespace(
                active=frontend_active,
            ),
            interaction=interaction,
        ),
        approval_coordinator=ApprovalCoordinator(interaction),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(),
            execution_policy=execution_policy,
        ),
        turn_session_context=turn_session_context,
        turn_session_state=turn_session_state,
        await_cleanup=await_cleanup,
        remember_last_assistant_reply=remembered.append,
        remembered=remembered,
        conversation=SimpleNamespace(
            queue_turn_context=lambda contexts: queued_context.append(
                tuple(contexts)
            ),
        ),
        queued_context=queued_context,
        runtime_services=SimpleNamespace(
            create_effect_journal=Mock(return_value=effect_journal),
        ),
        _effect_directory=effect_directory,
    )


async def _run_stream(
    monkeypatch,
    events: list[dict[str, typing.Any]],
    *,
    hooks: HookRuntime | None = None,
    hook_scope_factory: typing.Callable[
        [HookExecutionContext],
        HookExecutionScope,
    ] | None = None,
    session_started: bool = False,
    child_agent: bool = False,
    frontend_active: bool = True,
    additional_context: tuple[str, ...] = (),
    request_skills: tuple[dict[str, str], ...] | None = None,
    attachments: tuple[dict[str, typing.Any], ...] = (),
    extras: dict[str, typing.Any] | None = None,
    stream_factory: typing.Callable[
        ..., typing.AsyncIterator[typing.Any]
    ] | None = None,
    on_turn_input_event: typing.Callable[[typing.Any], typing.Any] | None = None,
    on_turn_stream_end: typing.Callable[[str], None] | None = None,
    on_turn_interrupted: typing.Callable[[], None] | None = None,
    host_state: SimpleNamespace | None = None,
    show_hook_lifecycle: bool = False,
    permissions: PermissionSettings | None = None,
    effect_journal=None,
    environment_snapshot: dict[str, typing.Any] | None = None,
    output_control: _OutputControl | None = None,
    observe_only: bool = False,
    observation_replay_target_seq: int | None = None,
    terminal_ready: asyncio.Event | None = None,
    keep_open: bool = False,
) -> tuple[RunResult, SimpleNamespace]:
    if stream_factory is None:
        async def stream_chat(*_args, **_kwargs):
            for payload in events:
                if payload.get("type") == "turn.completed" and terminal_ready is not None:
                    await terminal_ready.wait()
                for batched_payload in _batched_stream_payloads(payload):
                    yield parse_stream_event(batched_payload)
            if keep_open:
                await asyncio.Event().wait()
    else:
        stream_chat = stream_factory

    monkeypatch.setattr(
        stream,
        "interrupt_turn",
        AsyncMock(return_value=SimpleNamespace(status="accepted")),
        raising=False,
    )
    host = host_state or _host(
        frontend_active=frontend_active,
        effect_journal=effect_journal,
    )

    class ModelCapabilityStub:
        """把测试流工厂适配到正式模型能力端口。"""

        def __init__(self) -> None:
            self.last_stream = None
            self.last_request: ModelStreamRequest | None = None
            self.last_observation: TurnObservationRequest | None = None
            self.stream_calls = 0
            self.observe_calls = 0

        class _EventStream(object):
            """为测试迭代器补齐模型流关闭端口。"""

            def __init__(
                self,
                iterator,
                request: ModelStreamRequest | TurnObservationRequest,
                *,
                on_recovery_status=None,
            ) -> None:
                self._iterator = iterator
                self._reducer = CanonicalItemReducer(
                    cid=request.cid,
                    sid=request.sid,
                    turn_id=request.turn_id,
                )
                self.end_reason = getattr(iterator, "end_reason", None)
                self.last_event_seq = getattr(iterator, "last_event_seq", 0)
                self.current_item = None
                self.closed = False
                self._on_recovery_status = on_recovery_status
                self._replay_target_seq = (
                    request.replay_target_seq
                    if isinstance(request, TurnObservationRequest)
                    else None
                )

            @property
            def canonical_items(self):
                return self._reducer.canonical_items

            @property
            def canonical_item_history(self):
                return self._reducer.item_history

            @property
            def pending_approval_items(self):
                return self._reducer.pending_approval_items

            @property
            def assistant_text(self) -> str:
                return self._reducer.assistant_text

            @property
            def sources(self):
                return self._reducer.sources

            def __aiter__(self):
                return self._events()

            async def _events(self):
                projected_event_seq = 0
                if (
                    self._replay_target_seq is not None
                    and self._on_recovery_status is not None
                ):
                    await self._on_recovery_status("replaying", 0)
                async for event in self._iterator:
                    if event.type not in {"ping", "stream.gap"}:
                        projected_event_seq += 1
                        projected_event = dataclasses.replace(
                            event,
                            event_seq=projected_event_seq,
                        )
                    else:
                        projected_event = event
                    self.current_item = self._reducer.apply(projected_event)
                    yield event
                    if (
                        self._replay_target_seq is not None
                        and event.event_seq is not None
                        and event.event_seq >= self._replay_target_seq
                        and self._on_recovery_status is not None
                    ):
                        await self._on_recovery_status(
                            "caught_up",
                            event.event_seq,
                        )
                        self._replay_target_seq = None

            async def aclose(self) -> None:
                self.closed = True
                close = getattr(self._iterator, "aclose", None)
                if close is not None:
                    await close()

        def stream(
            self,
            request: ModelStreamRequest,
            *,
            on_recovery_status=None,
            on_approval_snapshot=None,
        ):
            """按旧测试工厂签名展开冻结请求。"""
            self.stream_calls += 1
            self.last_request = request
            request_options = request.option_values()
            metadata = request.metadata_value()
            metadata.update({"cid": request.cid, "sid": request.sid})
            request_options["metadata"] = metadata
            request_options["turn_id"] = request.turn_id
            iterator = stream_chat(
                request.pref_config_value(),
                request.message,
                request.tool_values(),
                attachments=request.attachment_values() or None,
                timeout=request.timeout,
                initial_event_seq=0,
                on_recovery_status=on_recovery_status,
                on_approval_snapshot=on_approval_snapshot,
                **request_options,
            )
            self.last_stream = self._EventStream(iterator, request)
            return self.last_stream

        def observe(
            self,
            request: TurnObservationRequest,
            *,
            on_recovery_status=None,
            on_approval_snapshot=None,
        ):
            """只创建测试事件观察流，不调用模型提交入口。"""
            self.observe_calls += 1
            self.last_observation = request
            iterator = stream_chat(
                on_recovery_status=on_recovery_status,
                on_approval_snapshot=on_approval_snapshot,
            )
            self.last_stream = self._EventStream(
                iterator,
                request,
                on_recovery_status=on_recovery_status,
            )
            return self.last_stream

        async def interrupt_turn(self, **kwargs):
            """把控制命令测试替身连接到当前协议请求替身。"""
            return await stream.interrupt_turn(**kwargs)

        async def steer_turn(self, **kwargs):
            """提供完整协议端口所需的引导命令形状。"""
            del kwargs
            return SimpleNamespace(status="accepted")

        async def reconcile_turn_inputs(self, **kwargs):
            """提供完整协议端口所需的输入对账命令形状。"""
            del kwargs
            return SimpleNamespace(
                turn_id="turn_test",
                turn_exists=True,
                terminal=None,
                committed_ids=(),
                pending_ids=(),
                retry_ids=(),
                unknown_ids=(),
            )

        async def get_turn_status(self, **kwargs):
            """提供完整协议端口所需的轮次状态查询形状。"""
            del kwargs
            return SimpleNamespace(
                cid="cid_test",
                sid="sid_test",
                turn_id="turn_test",
                run_id="run_test",
                status="running",
                terminal=None,
                attempt=1,
                version=1,
                last_event_seq=0,
                created_at=0.0,
                updated_at=0.0,
                error="",
            )

        async def fork_session(self, **kwargs):
            """提供完整协议端口所需的会话分支命令形状。"""
            del kwargs
            return SimpleNamespace(
                request_id="request_test",
                source_cid="cid_test",
                source_sid="sid_test",
                prompt_source="none",
                cid="cid_forked",
                sid="sid_forked",
                copied_items=0,
                copied_turns=0,
            )

        async def post_tool_result(self, *args, **kwargs):
            """把工具结果命令测试替身连接到当前协议请求替身。"""
            return await stream.post_tool_result(*args, **kwargs)

        async def get_tool_result_status(self, **kwargs):
            """把工具状态查询测试替身连接到当前协议请求替身。"""
            return await stream.get_tool_result_status(**kwargs)

        async def renew_tool_result(self, **kwargs):
            """把托管工具续期命令测试替身连接到当前协议请求替身。"""
            del kwargs
            return {"status": "renewed"}

        async def post_tool_approval(self, *args, **kwargs):
            """把审批命令测试替身连接到当前协议请求替身。"""
            return await stream.post_tool_approval(*args, **kwargs)

        async def post_effect_reconciliation(self, **kwargs):
            """把效果核对命令测试替身连接到当前协议请求替身。"""
            return await stream.post_effect_reconciliation(**kwargs)

    host.runtime_services.model_capability = ModelCapabilityStub()
    permissions = permissions or preset_permissions("auto")
    root_agent = AgentContext.root("sid_test")
    turn_context = TurnContext.create(
        agent=(
            root_agent.child("worker", "worker", agent_id="agent_child")
            if child_agent
            else root_agent
        ),
        cid="cid_test",
        sid="sid_test",
        source="test",
        pref_config={},
        cwd=".",
        permissions=permissions,
        approval_coordinator=host.approval_coordinator,
        execution_policy=host.workspace_runtime.execution_policy,
        approval_ledger=ApprovalCallLedger(),
        transcript_factory=host.transcripts.writer,
        cleanup=host,
        patch_preview=getattr(
            host.workspace_runtime.coding,
            "preview_patch",
            None,
        ),
        session_context=host.turn_session_context,
        session_state=host.turn_session_state,
        turn_id="turn_test",
        session_started=session_started,
        session_start_reason="initial" if session_started else "",
    )
    hook_context = HookExecutionContext.from_turn(turn_context)
    hook_scope = (
        hook_scope_factory(hook_context)
        if hook_scope_factory is not None
        else HookExecutionScope(
            context=hook_context,
            dispatcher=hooks or HookRuntime.empty(),
        )
    )
    turn_execution = TurnExecution(
        context=turn_context,
        message="hello",
        hook_scope=hook_scope,
        additional_context=additional_context,
        input_payload=build_turn_input_payload(
            "hello",
            attachments=attachments,
            extras=extras,
        ),
    )
    def session_factory(
        _path: str,
        *,
        context: OutputSurfaceContext,
        animate: bool,
    ) -> OutputSession:
        """创建与当前测试 Turn scope 完全匹配的输出会话。"""
        _ = animate
        output_session = _output_session(
            context,
            show_hook_lifecycle=show_hook_lifecycle,
            control=output_control,
        )
        host.output_session = output_session
        return output_session

    stream_options = {
        "exec_env": environment_snapshot,
        "skills": (
            list(request_skills)
            if request_skills is not None
            else [{"name": "test"}]
        ),
        "turn_execution": turn_execution,
        "session_factory": session_factory,
    }
    stream_options["model_capability"] = host.runtime_services.model_capability
    if observe_only:
        stream_options["turn_observer"] = host.runtime_services.model_capability
        if observation_replay_target_seq is not None:
            stream_options["observation_replay_target_seq"] = (
                observation_replay_target_seq
            )
    stream_options["protocol_client"] = host.runtime_services.model_capability
    stream_options["effect_journal_factory"] = (
        host.runtime_services.create_effect_journal
    )
    stream_options["tool_execution"] = McpToolExecutionAdapter()
    if attachments:
        stream_options["attachments"] = list(attachments)
    if extras:
        stream_options["extras"] = dict(extras)
    if on_turn_input_event is not None:
        stream_options["on_turn_input_event"] = on_turn_input_event
    if on_turn_stream_end is not None:
        stream_options["on_turn_stream_end"] = on_turn_stream_end
    if on_turn_interrupted is not None:
        stream_options["on_turn_interrupted"] = on_turn_interrupted

    run_stream = (
        observation.observe_stream_turn
        if observe_only
        else stream.stream_turn
    )
    result = await run_stream(
        SimpleNamespace(),
        {},
        [],
        **stream_options,
    )
    return result, host


@pytest.mark.anyio
async def test_stream_returns_failed_result(monkeypatch) -> None:
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "turn.completed",
            "status": "failed",
            "error": "request failed",
        },
    ])

    assert result.status == "failed"
    assert result.error == "request failed"
    assert result.exit_code == 1
    assert TurnTerminal(
        surface_id=host.output_session.context.surface_id,
        turn_id="turn_test",
        status="failed",
    ) in host.output_session.activity.items


@pytest.mark.anyio
async def test_stream_without_terminal_event_is_incomplete(monkeypatch) -> None:
    result, host = await _run_stream(monkeypatch, [])

    assert result.status == "incomplete"
    assert result.error == "stream ended before turn completion"
    assert TurnTerminal(
        surface_id=host.output_session.context.surface_id,
        turn_id="turn_test",
        status="failed",
    ) in host.output_session.activity.items


@pytest.mark.anyio
async def test_stream_emits_assistant_boundary_before_structured_output(monkeypatch) -> None:
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "segment_id": "first-item",
            "text": "first",
        },
        {"type": "text.done", "segment_id": "first-item"},
        {
            "type": "tool.builtin.call",
            "builtin_call_id": "builtin-1",
            "builtin_type": "web_search_call",
        },
        {
            "type": "tool.builtin.done",
            "builtin_call_id": "builtin-1",
            "builtin_type": "web_search_call",
        },
        {
            "type": "text.delta",
            "segment_id": "second-item",
            "text": "second",
        },
        {"type": "text.done", "segment_id": "second-item"},
        {"type": "turn.completed"},
    ])

    assert result.status == "completed"
    assert host.output_session.content.items == [
        AssistantTextDelta("first", response_identity()),
        AssistantSegmentCompleted(response_identity()),
        AssistantOutputBoundary(),
        AssistantTextDelta("second", response_identity()),
        AssistantSegmentCompleted(response_identity()),
        SourcesOutput(()),
    ]
    assert len(host.output_session.activity.batches) == 1
    assert tuple(
        type(item)
        for item in host.output_session.activity.batches[0]
    ) == (ToolCompleted, ModelWaitRequested)


@pytest.mark.anyio
async def test_stream_commits_output_before_tool_round_transition(
    monkeypatch,
) -> None:
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "round": 1,
            "segment_id": "round-one-item",
            "text": "first",
        },
        {
            "type": "tool.builtin.call",
            "round": 2,
            "builtin_call_id": "builtin-1",
            "builtin_type": "web_search_call",
        },
        {
            "type": "text.delta",
            "round": 2,
            "segment_id": "round-two-item",
            "text": "second",
        },
        {
            "type": "text.done",
            "round": 2,
            "segment_id": "round-two-item",
        },
        {
            "type": "turn.completed",
            "round": 2,
        },
    ])

    assert result == RunResult(
        status="completed",
        assistant_text="first\nsecond",
    )
    assert host.output_session.content.items == [
        AssistantTextDelta("first", response_identity(round_no=1)),
        AssistantOutputBoundary(),
        AssistantTextDelta("second", response_identity(round_no=2)),
        AssistantSegmentCompleted(response_identity(round_no=2)),
        SourcesOutput(()),
    ]
    assert [
        entry["payload"]
        for entry in host.transcripts.entries
        if entry["actor"] == "assistant"
    ] == [
        {
            "content": "first",
            "item_id": "round-one-item",
            "presentation_epoch": 1,
            "round": 1,
            "attempt": 1,
        },
        {
            "content": "second",
            "item_id": "round-two-item",
            "presentation_epoch": 1,
            "round": 2,
            "attempt": 1,
        },
    ]


@pytest.mark.anyio
async def test_stream_commits_multiple_item_identities_without_boundary(
    monkeypatch,
) -> None:
    _result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "round": 1,
            "segment_id": "item-one",
            "text": "first",
        },
        {
            "type": "text.delta",
            "round": 2,
            "segment_id": "item-two",
            "text": "second",
        },
        {
            "type": "text.done",
            "round": 2,
            "segment_id": "item-two",
        },
        {"type": "turn.completed", "round": 2},
    ])

    messages = [
        entry["payload"]["content"]
        for entry in host.transcripts.entries
        if entry["event"] == "message.created" and entry["actor"] == "assistant"
    ]
    assert messages == ["first", "second"]
    assert host.output_session.content.items == [
        AssistantTextDelta("first", response_identity(round_no=1)),
        AssistantOutputBoundary(),
        AssistantTextDelta("second", response_identity(round_no=2)),
        AssistantSegmentCompleted(response_identity(round_no=2)),
        SourcesOutput(()),
    ]


@pytest.mark.anyio
async def test_stream_updates_transcript_when_final_text_arrives_after_boundary(
    monkeypatch,
) -> None:
    _result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "segment_id": "item-1",
            "text": "partial",
        },
        {
            "type": "tool.output",
            "name": "remote_tool",
            "call_id": "call-1",
            "status": "completed",
            "result": {"ok": True},
        },
        {
            "type": "text.done",
            "segment_id": "item-1",
            "final_text": "complete",
        },
        {"type": "turn.completed"},
    ])

    assistant_entries = [
        entry
        for entry in host.transcripts.entries
        if entry["actor"] == "assistant"
    ]
    assert [entry["event"] for entry in assistant_entries] == [
        "message.created",
        "message.updated",
    ]
    assert assistant_entries[-1]["payload"]["content"] == "complete"


@pytest.mark.anyio
async def test_stream_reports_client_tool_result_from_turn_context(monkeypatch) -> None:
    invocations = []
    posted = []

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        invocations.append(invocation)
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="done",
                call_id=invocation.call_id,
                fields=_client_result_fields(invocation),
            )
        )

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, host = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "cid": "cid_test",
            "sid": "sid_test",
            "call_id": "call-client",
            "name": "test_tool",
            "arguments": {"value": 1},
            "reason": "模型需要调用客户端工具。",
        }),
        {"type": "turn.completed"},
    ])

    assert result.status == "completed"
    assert len(host.output_session.activity.batches) == 1
    assert tuple(
        type(item)
        for item in host.output_session.activity.batches[0]
    ) == (ToolCompleted, ModelWaitRequested)
    assert invocations[0].turn.cid == "cid_test"
    assert invocations[0].turn.sid == "sid_test"
    posted_args, posted_kwargs = posted[0]
    assert posted_args == (
        "cid_test",
        "sid_test",
        "call-client",
        "test_tool",
        True,
        {
            "ok": True,
            "tool": "test_tool",
            "source": "client",
            "args": {"value": 1},
            "text": "done",
            "attachments": [],
            "data": {},
        },
    )
    assert "arguments" not in posted_kwargs


@pytest.mark.anyio
async def test_stream_reconciles_uncertain_tool_result_before_failing(
    monkeypatch,
) -> None:
    posted = 0
    reconciled = []
    effect_posts = []
    terminal_ready = asyncio.Event()

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="done",
                call_id=invocation.call_id,
                fields=_client_result_fields(invocation),
            )
        )

    async def post_tool_result(*_args, **_kwargs):
        nonlocal posted
        posted += 1
        raise ToolResultRequestError(
            "tool_result_reconciliation_required",
            "tool result requires effect reconciliation",
            status_code=409,
            retryable=True,
        )

    async def get_status(**_kwargs):
        return {
            "cid": "cid_test",
            "sid": "sid_test",
            "call_id": "call-reconcile",
            "turn_id": "turn_test",
            "name": "test_tool",
            "tool_status": "waiting_result",
            "completion_mode": "interactive",
            "turn_status": "reconciliation_required",
            "result_received": False,
            "effect_id": "effect-reconcile",
            "effect_status": "unknown",
            "reconciliation_required": True,
        }

    async def reconcile(_runner, effect_id):
        reconciled.append(effect_id)
        return False

    async def post_effect_reconciliation(**kwargs):
        effect_posts.append(kwargs)
        terminal_ready.set()
        return {"ok": True, "status": "reconciled", "effect": {}}

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    monkeypatch.setattr(stream, "get_tool_result_status", get_status)
    monkeypatch.setattr(
        stream.ClientToolCallRunner,
        "reconcile_known_effect",
        reconcile,
    )
    monkeypatch.setattr(stream, "post_effect_reconciliation", post_effect_reconciliation)

    result, _host_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-reconcile",
            "name": "test_tool",
            "arguments": {"value": 1},
        }),
        {"type": "turn.completed"},
    ], terminal_ready=terminal_ready)

    assert result.status == "completed"
    assert posted == 1
    assert reconciled == ["effect-reconcile"]
    assert effect_posts[0]["effect_id"] == "effect-reconcile"
    assert effect_posts[0]["resolution"] == "committed"
    assert effect_posts[0]["result_payload"]["result"]["tool"] == "test_tool"


@pytest.mark.anyio
async def test_stream_retries_unknown_ack_with_same_request_id(monkeypatch) -> None:
    request_ids = []
    statuses = 0
    terminal_ready = asyncio.Event()

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="done",
                call_id=invocation.call_id,
                fields=_client_result_fields(invocation),
            )
        )

    async def post_tool_result(*_args, **kwargs):
        request_ids.append(kwargs["request_id"])
        if len(request_ids) == 1:
            raise ToolResultRequestError(
                "tool_result_ack_invalid",
                "tool result acknowledgement is invalid",
                status_code=200,
            )
        terminal_ready.set()
        return {}

    async def get_status(**_kwargs):
        nonlocal statuses
        statuses += 1
        return {
            "cid": "cid_test",
            "sid": "sid_test",
            "call_id": "call-ack",
            "turn_id": "turn_test",
            "name": "test_tool",
            "tool_status": "waiting_result",
            "completion_mode": "interactive",
            "turn_status": "active",
            "result_received": False,
            "reconciliation_required": False,
        }

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    monkeypatch.setattr(stream, "get_tool_result_status", get_status)

    result, _host_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-ack",
            "name": "test_tool",
            "arguments": {},
        }),
        {"type": "turn.completed"},
    ], terminal_ready=terminal_ready)

    assert result.status == "completed"
    assert statuses == 1
    assert len(request_ids) == 2
    assert request_ids[0] == request_ids[1]


@pytest.mark.anyio
async def test_stream_waits_for_interrupted_terminal_after_tool_result_closes(
    monkeypatch,
) -> None:
    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="done",
                call_id=invocation.call_id,
                fields=_client_result_fields(invocation),
            )
        )

    async def post_tool_result(*_args, **_kwargs):
        raise ToolResultRequestError(
            "tool_call_turn_closed",
            "turn no longer accepts tool results",
        )

    status_query = AsyncMock()
    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    monkeypatch.setattr(stream, "get_tool_result_status", status_query)

    result, host = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-closed",
            "name": "test_tool",
            "arguments": {},
        }),
        {"type": "turn.completed", "status": "interrupted"},
    ])

    assert result.status == "interrupted"
    assert result.error is None
    status_query.assert_not_awaited()
    failure_views = [
        item
        for item in host.output_session.presentation.items
        if isinstance(item, FailureView)
    ]
    assert failure_views == []


@pytest.mark.anyio
async def test_stream_does_not_accept_completed_after_tool_result_closes(
    monkeypatch,
) -> None:
    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="done",
                call_id=invocation.call_id,
                fields=_client_result_fields(invocation),
            )
        )

    async def post_tool_result(*_args, **_kwargs):
        raise ToolResultRequestError(
            "tool_call_turn_closed",
            "turn no longer accepts tool results",
        )

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, host = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-closed-completed",
            "name": "test_tool",
            "arguments": {},
        }),
        {"type": "turn.completed", "status": "completed"},
    ])

    assert result.status == "incomplete"
    assert result.error == (
        "tool_call_turn_closed: turn no longer accepts tool results"
    )
    assert result.error_code == "tool_call_turn_closed"
    assert not any(
        isinstance(item, FailureView)
        for item in host.output_session.presentation.items
    )


@pytest.mark.anyio
async def test_stream_does_not_synthesize_interrupt_from_escaped_tool_error(
    monkeypatch,
) -> None:
    async def dispatch(_boundary, _event):
        raise ProtocolCommandError(
            "tool_call_turn_closed",
            "turn no longer accepts tool results",
        )

    monkeypatch.setattr(stream.ToolTurnBoundary, "dispatch", dispatch)

    result, host = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-escaped-closure",
            "name": "test_tool",
            "arguments": {},
        }),
    ])

    assert result.status == "incomplete"
    assert result.error_code == "tool_call_turn_closed"
    failure_views = [
        item
        for item in host.output_session.presentation.items
        if isinstance(item, FailureView)
    ]
    assert failure_views[-1].phase == "turn.incomplete"


@pytest.mark.anyio
async def test_stream_does_not_reopen_approval_after_turn_closes(
    monkeypatch,
) -> None:
    approval_posts = []

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))
        raise ProtocolCommandError(
            "approval_not_pending",
            "tool approval is not pending",
            details={
                "call_id": "call-approval-closed",
                "approval_id": "approval-closed",
            },
        )

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)
    host = _host()
    approval_event = _durable_tool_call({
        "type": "tool.approval_required",
        "call_id": "call-approval-closed",
        "approval_id": "approval-closed",
        "kind": "command",
        "command": ["echo", "ready"],
        "cwd": ".",
        "reason": "Run the requested command.",
    })

    result, host_state = await _run_stream(
        monkeypatch,
        [
            {**approval_event, "event_seq": 1},
            {**approval_event, "event_seq": 2},
            {
                "type": "turn.completed",
                "event_seq": 3,
                "status": "interrupted",
            },
        ],
        host_state=host,
    )

    assert result.status == "interrupted"
    assert result.error is None
    assert len(approval_posts) == 1
    host_state.frontend.interaction.present_approval.assert_awaited_once()
    assert not any(
        isinstance(item, FailureView)
        for item in host_state.output_session.presentation.items
    )


@pytest.mark.anyio
async def test_stream_restores_approvals_after_closed_snapshot_entry(
    monkeypatch,
) -> None:
    approval_posts = []

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))
        if len(approval_posts) == 1:
            raise ProtocolCommandError(
                "approval_decision_conflict",
                "first approval has a different decision",
                details={
                    "call_id": "call-snapshot-closed",
                    "approval_id": "approval-snapshot-closed",
                },
            )

    snapshot = ToolApprovalSnapshot(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        turn_status="waiting_approval",
        terminal=None,
        last_event_seq=2,
        approvals=tuple(
            ToolApprovalSnapshotItem(
                approval_id=f"approval-snapshot-{suffix}",
                turn_id="turn_test",
                call_id=f"call-snapshot-{suffix}",
                kind="command",
                approval={
                    "approval_id": f"approval-snapshot-{suffix}",
                    "call_id": f"call-snapshot-{suffix}",
                    "turn_id": "turn_test",
                    "kind": "command",
                    "command": f"echo {suffix}",
                    "cwd": ".",
                },
                status="pending",
                ack=None,
            )
            for suffix in ("closed", "pending")
        ),
    )

    async def stream_chat(*_args, on_approval_snapshot=None, **_kwargs):
        assert on_approval_snapshot is not None
        await on_approval_snapshot(snapshot)
        yield parse_stream_event({
            "type": "turn.completed",
            "status": "interrupted",
        })

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)
    host = _host()
    host.frontend.interaction.present_approval = AsyncMock(
        side_effect=("decline", "decline"),
    )

    result, host_state = await _run_stream(
        monkeypatch,
        (),
        stream_factory=stream_chat,
        host_state=host,
    )

    assert result.status == "interrupted"
    assert result.error is None
    assert len(approval_posts) == 2
    assert host_state.frontend.interaction.present_approval.await_count == 2


@pytest.mark.anyio
async def test_stream_reports_plan_result_after_local_execution(
    monkeypatch,
    tmp_path,
) -> None:
    posted = []
    effect_journal = _open_effect_journal(tmp_path / "effects.db")

    async def handle(_runner, *, invocation):
        assert invocation.name == PLAN_STEPS_TOOL
        return PlanExecutionReport(
            ok=True,
            text="planned",
            data={"steps": 1},
            attachments=[],
            cost_ms=5,
            results=[],
            additional_context=("nested tool context",),
        )

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.PlanToolCallRunner, "handle", handle)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _host_state = await _run_stream(
        monkeypatch,
        [
            _durable_tool_call({
                "type": "tool.call",
                "cid": "cid_test",
                "sid": "sid_test",
                "call_id": "call-plan",
                "name": PLAN_STEPS_TOOL,
                "arguments": {"steps": []},
            }),
            {"type": "turn.completed"},
        ],
        effect_journal=effect_journal,
    )

    assert result.status == "completed"
    assert len(posted) == 1
    assert posted[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call-plan",
        PLAN_STEPS_TOOL,
        True,
    )
    assert posted[0][0][5]["data"] == {"steps": 1}
    assert posted[0][1]["additional_context"] == ("nested tool context",)


@pytest.mark.anyio
async def test_stream_queues_pre_tool_context_after_operation_error(
    monkeypatch,
    tmp_path,
) -> None:
    effect_journal = _open_effect_journal(tmp_path / "effects.db")
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": "inspect protected paths",
                },
            })

    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [
                _hook("pre", matcher=PLAN_STEPS_TOOL),
            ],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    async def fail(_runner, *, invocation):
        _ = invocation
        raise RuntimeError("plan operation failed")

    monkeypatch.setattr(stream.PlanToolCallRunner, "handle", fail)

    result, host_state = await _run_stream(
        monkeypatch,
        [_durable_tool_call({
            "type": "tool.call",
            "call_id": "call-plan",
            "name": PLAN_STEPS_TOOL,
            "arguments": {"steps": []},
        })],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        effect_journal=effect_journal,
        keep_open=True,
    )

    assert result.status == "failed"
    assert result.additional_context == ("inspect protected paths",)
    assert host_state.queued_context == [("inspect protected paths",)]


@pytest.mark.anyio
async def test_stream_consumes_input_and_terminal_during_long_tool(monkeypatch) -> None:
    started = asyncio.Event()
    released = asyncio.Event()
    observed = []

    async def execute(_runner, _invocation, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    async def stream_chat(*_args, **_kwargs):
        call = _durable_tool_call({
            "type": "tool.call", "call_id": "call-long", "name": "test_tool",
            "arguments": {},
        })
        for payload in _batched_stream_payloads(call):
            yield parse_stream_event(payload)
        await started.wait()
        yield parse_stream_event({
            "type": "turn.input.accepted", "client_message_id": "message_queued_01",
        })
        yield parse_stream_event({"type": "turn.completed", "status": "interrupted"})

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    result, _host_state = await asyncio.wait_for(_run_stream(
        monkeypatch, [], stream_factory=stream_chat,
        on_turn_input_event=observed.append,
    ), timeout=2.0)
    assert result.status == "interrupted"
    assert [event.type for event in observed] == ["turn.input.accepted", "turn.completed"]
    assert released.is_set()


@pytest.mark.anyio
async def test_post_tool_hook_cannot_replace_plan_result_for_model(
    monkeypatch,
    tmp_path,
) -> None:
    effect_journal = _open_effect_journal(tmp_path / "effects.db")
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={
                "systemMessage": "Use the replacement result.",
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "explain replacement",
                },
            })

    definitions = resolve_hook_definitions(
        {"PostToolUse": [_hook("replace", matcher=PLAN_STEPS_TOOL)]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    posted = []

    async def handle(_runner, *, invocation):
        assert invocation.name == PLAN_STEPS_TOOL
        return PlanExecutionReport(
            ok=True,
            text="planned",
            data={"steps": 1},
            attachments=[],
            cost_ms=5,
            results=[],
        )

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.PlanToolCallRunner, "handle", handle)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _host_state = await _run_stream(
        monkeypatch,
        [
            _durable_tool_call({
                "type": "tool.call",
                "call_id": "call-plan",
                "name": PLAN_STEPS_TOOL,
                "arguments": {"steps": []},
            }),
            {"type": "turn.completed"},
        ],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        effect_journal=effect_journal,
    )

    assert result.status == "completed"
    assert posted[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call-plan",
        PLAN_STEPS_TOOL,
        True,
    )
    assert posted[0][0][5]["data"] == {"steps": 1}
    assert posted[0][1]["additional_context"] == ("explain replacement",)
    assert "system_message" not in posted[0][1]
