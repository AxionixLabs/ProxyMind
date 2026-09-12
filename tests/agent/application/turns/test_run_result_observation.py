# -*- coding: utf-8 -*-

"""验证已存在 Turn 的观察、重放接管与显式请求字段。"""


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
        restore_context_usage = AsyncMock()

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
) -> tuple[RunResult, SimpleNamespace]:
    if stream_factory is None:
        async def stream_chat(*_args, **_kwargs):
            for payload in events:
                for batched_payload in _batched_stream_payloads(payload):
                    yield parse_stream_event(batched_payload)
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
async def test_observed_turn_attaches_without_replaying_submit_hooks(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.events: list[str] = []

        async def execute(self, definition, _payload):
            self.events.append(definition.event)
            return HookCommandOutput(data={})

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "SessionStart": [_hook("start", matcher="startup")],
            "UserPromptSubmit": [_hook("prompt")],
            "Stop": [_hook("stop")],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, host = await _run_stream(
        monkeypatch,
        [
            {"type": "text.delta", "text": "observed"},
            {"type": "text.done"},
            {"type": "turn.completed"},
        ],
        hooks=HookRuntime(definitions, command_runner=runner),
        session_started=True,
        observe_only=True,
    )

    capability = host.runtime_services.model_capability
    assert result.status == "completed"
    assert result.assistant_text == "observed"
    assert capability.stream_calls == 0
    assert capability.observe_calls == 1
    assert capability.last_request is None
    assert capability.last_observation == TurnObservationRequest(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        timeout=60.0,
    )
    assert runner.events == ["Stop"]


@pytest.mark.anyio
async def test_observed_replay_does_not_repeat_completed_client_tool(
    monkeypatch,
) -> None:
    execute = AsyncMock()
    post_result = AsyncMock(return_value={})
    get_status = AsyncMock(return_value={
        "name": "test_tool",
        "tool_status": "result_received",
        "result_received": True,
        "reconciliation_required": False,
    })
    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_result)
    monkeypatch.setattr(stream, "get_tool_result_status", get_status)

    result, host = await _run_stream(
        monkeypatch,
        [
            _durable_tool_call({
                "type": "tool.call",
                "event_seq": 2,
                "call_id": "call-replayed",
                "name": "test_tool",
                "arguments": {"value": 1},
            }),
            {
                "type": "turn.completed",
                "event_seq": 4,
                "last_event_seq": 4,
            },
        ],
        observe_only=True,
        observation_replay_target_seq=4,
    )

    assert result.status == "completed"
    execute.assert_not_awaited()
    post_result.assert_not_awaited()
    get_status.assert_awaited_once_with(
        cid="cid_test",
        sid="sid_test",
        call_id="call-replayed",
    )
    assert not any(
        entry["event"] in {"tool.started", "tool.completed"}
        for entry in host.transcripts.entries
    )
    assert len(host.output_session.activity.batches) == 1
    assert tuple(
        type(item)
        for item in host.output_session.activity.batches[0]
    ) == (ToolCompleted, ModelWaitRequested)


@pytest.mark.anyio
async def test_observed_replay_takes_over_unresolved_client_tool_after_catch_up(
    monkeypatch,
) -> None:
    execution_started = asyncio.Event()

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        execution_started.set()
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="recovered",
                call_id=invocation.call_id,
                fields=_client_result_fields(invocation, text="recovered"),
            )
        )

    post_result = AsyncMock(return_value={})
    get_status = AsyncMock(return_value={
        "name": "test_tool",
        "tool_status": "waiting_result",
        "result_received": False,
        "reconciliation_required": False,
    })
    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_result)
    monkeypatch.setattr(stream, "get_tool_result_status", get_status)

    result, _host = await _run_stream(
        monkeypatch,
        [
            _durable_tool_call({
                "type": "tool.call",
                "event_seq": 2,
                "call_id": "call-unresolved",
                "name": "test_tool",
                "arguments": {"value": 1},
            }),
            {"type": "text.delta", "event_seq": 4, "text": "continued"},
            {"type": "text.done", "event_seq": 5},
            {
                "type": "turn.completed",
                "event_seq": 6,
                "last_event_seq": 6,
            },
        ],
        observe_only=True,
        observation_replay_target_seq=3,
    )

    assert result.status == "completed"
    assert result.assistant_text == "continued"
    assert execution_started.is_set()
    post_result.assert_awaited_once()
    get_status.assert_awaited_once_with(
        cid="cid_test",
        sid="sid_test",
        call_id="call-unresolved",
    )


@pytest.mark.anyio
async def test_observed_turn_submits_only_a_stop_hook_continuation(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.stop_count = 0

        async def execute(self, _definition, _payload):
            self.stop_count += 1
            if self.stop_count == 1:
                return HookCommandOutput(data={
                    "decision": "block",
                    "reason": "continue queued turn once",
                })
            return HookCommandOutput(data={})

    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    submitted_messages: list[str] = []

    async def stream_with_continuation(*args, **kwargs):
        if args:
            submitted_messages.append(args[1])
            turn_id = kwargs["turn_id"]
            text = "continued"
        else:
            turn_id = "turn_test"
            text = "queued"
        yield parse_stream_event({
            "type": "text.delta",
            "turn_id": turn_id,
            "text": text,
        })
        yield parse_stream_event({
            "type": "turn.completed",
            "turn_id": turn_id,
        })

    result, host = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        stream_factory=stream_with_continuation,
        observe_only=True,
    )

    capability = host.runtime_services.model_capability
    assert result.status == "completed"
    assert result.assistant_text == "continued"
    assert capability.observe_calls == 1
    assert capability.stream_calls == 1
    assert submitted_messages == ["continue queued turn once"]


@pytest.mark.anyio
async def test_stream_passes_environment_as_explicit_model_request_field(
    monkeypatch,
) -> None:
    snapshot = {
        "snapshot_id": "envsnap_stream_test",
        "source": "client",
        "captured_at": "2026-08-29T12:00:00Z",
        "environment_id": "local",
        "cwd": "D:\\workspace\\project",
        "status": "available",
        "shell": {"name": "powershell", "syntax": "powershell"},
        "workspace": {"root": "D:\\workspace", "source": "client"},
    }

    _result, host = await _run_stream(
        monkeypatch,
        [
            {"type": "turn.completed", "status": "interrupted", "usage": {}},
        ],
        environment_snapshot=snapshot,
    )

    request = host.runtime_services.model_capability.last_request
    assert isinstance(request, ModelStreamRequest)
    assert request.environment_snapshot_value() == {
        **snapshot,
        "status_detail": None,
        "shell": {
            "name": "powershell",
            "syntax": "powershell",
            "executable": None,
            "prefix": [],
            "source": None,
        },
        "workspace": {
            "root": "D:\\workspace",
            "allowed_roots": [],
            "source": "client",
        },
        "tools": {},
        "providers": {},
        "extensions": {},
    }
    assert "exec_env" not in request.option_values()


@pytest.mark.anyio
async def test_interrupted_turn_notifies_before_stream_cleanup(monkeypatch) -> None:
    notifications: list[str] = []

    definitions = resolve_hook_definitions(
        {"Interrupt": [_hook("interrupt")]},
        source_scope="user",
        source_path=Path("hooks.json"),
    )

    class CommandRunner:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def execute(self, definition, _payload):
            self.events.append(definition.event)
            return HookCommandOutput(
                data={"systemMessage": "interrupt noted"},
            )

    runner = CommandRunner()

    result, host_state = await _run_stream(
        monkeypatch,
        [
            {
                "type": "turn.completed",
                "status": "interrupted",
                "usage": {},
            },
        ],
        hooks=HookRuntime(definitions, command_runner=runner),
        on_turn_interrupted=lambda: notifications.append("acknowledged"),
    )

    assert result.status == "interrupted"
    assert notifications == ["acknowledged"]
    assert runner.events == ["Interrupt"]
    assert host_state.transcripts.entries[-1]["event"] == "turn.interrupted"
