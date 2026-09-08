# -*- coding: utf-8 -*-

"""验证 RunResult 映射、重试、终态和对账生命周期。

这些场景共同覆盖一次运行的完整状态机，维持整体可防止终态契约分散。
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


def test_run_result_maps_status_to_exit_code() -> None:
    assert RunResult(status="completed").exit_code == 0
    assert RunResult(status="failed", error="failed").exit_code == 1
    assert RunResult(status="incomplete").exit_code == 1
    assert RunResult(status="interrupted").exit_code == 1
    assert RunResult(status="reconciliation_required").exit_code == 1


def test_run_result_preserves_nested_usage_and_terminal_metadata() -> None:
    usage = {"input_tokens": 4, "cache": {"read_tokens": 2}}
    result = RunResult(
        status="incomplete",
        usage=usage,
        response_id="msg_1",
        model="claude-test",
        route="messages",
        request_id="req_1",
        service_tier="standard",
        stop_reason="max_tokens",
        reason="max_output_tokens",
        can_continue=True,
    )
    usage["cache"]["read_tokens"] = 99
    dumped = result.to_dict()
    dumped["usage"]["cache"]["read_tokens"] = 100

    assert result.usage["cache"] == {"read_tokens": 2}
    assert dumped["response_id"] == "msg_1"
    assert dumped["route"] == "messages"
    assert dumped["stop_reason"] == "max_tokens"
    assert dumped["reason"] == "max_output_tokens"
    assert dumped["can_continue"] is True


def test_run_result_serializes_named_error_details_without_aliasing() -> None:
    details = {"status_code": 503, "transport": {"attempt": 2}}
    result = RunResult(
        status="failed",
        error="service unavailable",
        error_code="model_transport_http_error",
        error_details=details,
    )
    details["transport"]["attempt"] = 9
    dumped = result.to_dict()
    dumped["error_details"]["transport"]["attempt"] = 7

    assert result.error_details == {
        "status_code": 503,
        "transport": {"attempt": 2},
    }
    assert dumped["error_code"] == "model_transport_http_error"
    assert dumped["error_details"] == {
        "status_code": 503,
        "transport": {"attempt": 7},
    }


@pytest.mark.anyio
async def test_tool_runtime_forwards_callback_result(monkeypatch) -> None:
    expected = RunResult(status="completed", assistant_text="done")
    host = SimpleNamespace(
        external_mcp=SimpleNamespace(current=None),
        client_tools=object(),
        is_service_mcp_linked=lambda: False,
    )

    async def build_context(
        *,
        service_session,
        external_group,
        client_registry,
        builtin_registry,
    ):
        _ = (
            service_session,
            external_group,
            client_registry,
            builtin_registry,
        )
        return SimpleNamespace(session=object(), tools=[])

    async def user_flow(_session, _tools) -> RunResult:
        return expected

    monkeypatch.setattr(tool_runtime, "build_tool_context", build_context)

    runtime = tool_runtime.CompositeToolRuntime(ToolRuntimeSources(
        client_registry=lambda: host.client_tools,
        builtin_registry=lambda: None,
        external_group=lambda: None,
        service_linked=host.is_service_mcp_linked,
    ))
    result = await runtime.with_session({}, user_flow)

    assert result is expected


@pytest.mark.anyio
async def test_stream_returns_completed_result(monkeypatch) -> None:
    result, host = await _run_stream(monkeypatch, [
        {"type": "text.delta", "text": "answer"},
        {"type": "text.done"},
        {"type": "turn.completed", "usage": {"output_tokens": 3}},
    ])

    assert result == RunResult(
        status="completed",
        assistant_text="answer",
        usage={"output_tokens": 3},
    )
    assert host.remembered == ["answer"]
    assert host.runtime_services.model_capability.last_stream.closed is True
    assert host.output_session.content.items == [
        AssistantTextDelta("answer", response_identity()),
        AssistantSegmentCompleted(response_identity()),
        SourcesOutput(()),
    ]
    assert [
        entry["event"] for entry in host.transcripts.entries
    ] == [
        "turn.started",
        "message.created",
        "message.created",
        "turn.completed",
    ]
    assert host.transcripts.entries[1]["actor"] == "user"
    assert host.transcripts.entries[2] == {
        "event": "message.created",
        "actor": "assistant",
        "payload": {
            "content": "answer",
            "item_id": "segment_test",
            "presentation_epoch": 1,
            "round": 1,
            "attempt": 1,
        },
    }
    assert not hasattr(host, "hook_scope")


@pytest.mark.anyio
async def test_copy_snapshot_uses_latest_final_item_and_preserves_source(
    monkeypatch,
) -> None:
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.done",
            "segment_id": "commentary-item",
            "final_text": "analysis",
            "phase": "commentary",
        },
        {
            "type": "text.done",
            "segment_id": "answer-item",
            "final_text": "  final answer  \r\n",
            "phase": "final_answer",
        },
        {"type": "turn.completed"},
    ])

    assert result.assistant_text == "analysis\n  final answer"
    assert host.remembered == ["  final answer  \r\n"]
    assistant_entries = [
        entry
        for entry in host.transcripts.entries
        if entry["actor"] == "assistant"
    ]
    assert assistant_entries[-1]["payload"]["content"] == (
        "  final answer  \r\n"
    )


@pytest.mark.anyio
async def test_stream_projects_deduplicated_canonical_sources(monkeypatch) -> None:
    _result, host = await _run_stream(monkeypatch, [
        {
            "type": "tool.builtin.done",
            "builtin_call_id": "builtin-source",
            "builtin_type": "web_search_call",
            "status": "completed",
            "sources": [{"url": "https://example.com/tool"}],
            "source_count": 1,
        },
        {
            "type": "text.delta",
            "segment_id": "answer-item",
            "text": "answer",
        },
        {
            "type": "text.meta",
            "segment_id": "answer-item",
            "sources": [
                {"url": "https://example.com/tool"},
                {"url": "https://example.com/text"},
            ],
            "source_count": 2,
        },
        {"type": "text.done", "segment_id": "answer-item"},
        {"type": "turn.completed"},
    ])

    assert host.output_session.content.items[-1] == SourcesOutput((
        {"url": "https://example.com/tool"},
        {"url": "https://example.com/text"},
    ))


@pytest.mark.anyio
async def test_stream_requires_reconciliation_after_uncertain_transport(
    monkeypatch,
) -> None:
    async def failed_stream(*_args, **_kwargs):
        if False:
            yield None
        raise ModelCapabilityError(
            "model_transport_timeout",
            "model transport timed out",
            retryable=True,
            details={"exception_type": "TimeoutError"},
        )

    result, _host = await _run_stream(
        monkeypatch,
        [],
        stream_factory=failed_stream,
    )

    assert result == RunResult(
        status="reconciliation_required",
        error="model transport timed out",
        error_code="model_transport_timeout",
        error_details={"exception_type": "TimeoutError"},
    )


@pytest.mark.anyio
async def test_stream_treats_rejected_start_as_deterministic_failure(
    monkeypatch,
) -> None:
    async def failed_stream(*_args, **_kwargs):
        if False:
            yield None
        raise ModelCapabilityError(
            "turn_already_active",
            "another logical turn is active",
            details={
                "status_code": 409,
                "active_turn_id": "turn_active",
                "active_status": "running",
            },
        )

    result, _host = await _run_stream(
        monkeypatch,
        [],
        stream_factory=failed_stream,
    )

    assert result == RunResult(
        status="failed",
        error="another logical turn is active",
        error_code="turn_already_active",
        error_details={
            "status_code": 409,
            "active_turn_id": "turn_active",
            "active_status": "running",
        },
    )


@pytest.mark.anyio
async def test_output_open_failure_does_not_enter_closed_activity_surface(
    monkeypatch,
) -> None:
    result, host = await _run_stream(
        monkeypatch,
        [],
        output_control=_OutputControl(
            open_error=RuntimeError("output control open failed")
        ),
    )

    assert result.status == "failed"
    assert result.error == "RuntimeError: output control open failed"
    assert not host.output_session.is_open
    assert host.output_session.activity.items == []
    assert host.output_session.presentation.items == []


@pytest.mark.anyio
async def test_provider_retry_replaces_partial_answer_in_same_turn(monkeypatch) -> None:
    """验证 provider 断流后只保留新 attempt 正文并切换重试状态。"""
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "turn_id": "turn_test",
            "presentation_epoch": 1,
            "round": 1,
            "segment_id": "attempt-1",
            "text": "old partial",
        },
        {
            "type": "turn.retrying",
            "turn_id": "turn_test",
            "presentation_epoch": 1,
            "round": 1,
            "attempt": 2,
            "max_attempts": 3,
            "retry_in_ms": 20,
            "reason": "stream_reset",
        },
        {
            "type": "text.delta",
            "turn_id": "turn_test",
            "presentation_epoch": 1,
            "round": 1,
            "segment_id": "attempt-2",
            "text": "new answer",
        },
        {
            "type": "text.done",
            "turn_id": "turn_test",
            "presentation_epoch": 1,
            "segment_id": "attempt-2",
        },
        {"type": "turn.completed", "turn_id": "turn_test"},
    ])

    assert result.status == "completed"
    assert result.assistant_text == "new answer"
    assert host.output_session.content.items == [
        AssistantTextDelta("old partial", response_identity()),
        AssistantResponseSuperseded(
            turn_id="turn_test",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ),
        AssistantTextDelta("new answer", response_identity(attempt=2)),
        AssistantSegmentCompleted(response_identity(attempt=2)),
        SourcesOutput(()),
    ]
    surface_id = host.output_session.context.surface_id
    assert [
        item
        for item in host.output_session.activity.items
        if isinstance(item, RetryChanged)
    ] == [
        RetryChanged(
            surface_id=surface_id,
            turn_id="turn_test",
            source="provider",
            state="started",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ),
        RetryChanged(
            surface_id=surface_id,
            turn_id="turn_test",
            source="provider",
            state="completed",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ),
    ]
    assert [
        entry["event"]
        for entry in host.transcripts.entries
        if entry["actor"] == "assistant"
    ] == ["message.created", "message.superseded", "message.created"]


@pytest.mark.anyio
async def test_provider_retry_ignores_late_old_item_events(monkeypatch) -> None:
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "segment_id": "item-old",
            "text": "old",
        },
        {
            "type": "turn.retrying",
            "round": 1,
            "attempt": 2,
            "max_attempts": 3,
            "retry_in_ms": 0,
            "supersedes_item_id": "item-old",
        },
        {
            "type": "text.delta",
            "segment_id": "item-old",
            "text": "late old",
        },
        {
            "type": "text.delta",
            "segment_id": "item-new",
            "text": "new",
        },
        {"type": "text.done", "segment_id": "item-new"},
        {"type": "turn.completed"},
    ])

    assert [
        item
        for item in host.output_session.content.items
        if isinstance(item, AssistantTextDelta)
    ] == [
        AssistantTextDelta("old", response_identity()),
        AssistantTextDelta("new", response_identity(attempt=2)),
    ]
    assert result.assistant_text == "new"


@pytest.mark.anyio
async def test_provider_retry_preserves_completed_previous_model_round(
    monkeypatch,
) -> None:
    """验证跨模型 round 重试只替换当前 response 的正文与记录。"""
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "round": 1,
            "segment_id": "round-1-attempt-1",
            "text": "round one",
        },
        {
            "type": "text.done",
            "round": 1,
            "segment_id": "round-1-attempt-1",
        },
        {
            "type": "tool.output",
            "round": 1,
            "name": "remote_tool",
            "call_id": "call-1",
            "status": "completed",
            "result": {"ok": True},
        },
        {
            "type": "text.delta",
            "round": 2,
            "segment_id": "round-2-attempt-1",
            "text": "round two partial",
        },
        {
            "type": "turn.retrying",
            "round": 2,
            "attempt": 2,
            "max_attempts": 3,
            "retry_in_ms": 20,
        },
        {
            "type": "text.delta",
            "round": 2,
            "segment_id": "round-2-attempt-2",
            "text": "round two final",
        },
        {
            "type": "text.done",
            "round": 2,
            "segment_id": "round-2-attempt-2",
        },
        {"type": "turn.completed", "round": 2},
    ])

    assert result.assistant_text == "round one\nround two final"
    assistant_entries = [
        entry
        for entry in host.transcripts.entries
        if entry["actor"] == "assistant"
    ]
    assert [entry["event"] for entry in assistant_entries] == [
        "message.created",
        "message.created",
        "message.superseded",
        "message.created",
    ]
    assert assistant_entries[0]["payload"] == {
        "content": "round one",
        "item_id": "round-1-attempt-1",
        "presentation_epoch": 1,
        "round": 1,
        "attempt": 1,
    }
    assert assistant_entries[2]["payload"] == {
        "scope": "response",
        "presentation_epoch": 1,
        "round": 2,
        "attempt": 2,
        "reason": "stream_error",
    }


@pytest.mark.anyio
async def test_provider_retry_without_partial_answer_adds_no_output_block(
    monkeypatch,
) -> None:
    """验证首字节前重试不会制造空正文或额外 attempt 提示。"""
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "turn.retrying",
            "turn_id": "turn_test",
            "round": 1,
            "attempt": 2,
            "max_attempts": 3,
            "retry_in_ms": 20,
        },
        {"type": "text.delta", "turn_id": "turn_test", "text": "answer"},
        {"type": "text.done", "turn_id": "turn_test"},
        {"type": "turn.completed", "turn_id": "turn_test"},
    ])

    assert result.assistant_text == "answer"
    assert not any(
        isinstance(item, AssistantResponseSuperseded)
        for item in host.output_session.content.items
    )


@pytest.mark.anyio
async def test_provider_and_transport_retry_statuses_do_not_clear_each_other(
    monkeypatch,
) -> None:
    """验证两个重试来源重叠时只产生一次完整状态区间。"""
    async def overlapping_retry_stream(*_args, **kwargs):
        recovery_status = kwargs["on_recovery_status"]
        yield parse_stream_event({
            "type": "turn.retrying",
            "turn_id": "turn_test",
            "round": 1,
                "attempt": 2,
                "max_attempts": 3,
                "retry_in_ms": 20,
        })
        await recovery_status("reconnecting", 1)
        await recovery_status("replaying", 1)
        await recovery_status("caught_up", 1)
        yield parse_stream_event({
            "type": "text.delta",
            "turn_id": "turn_test",
            "text": "answer",
        })
        yield parse_stream_event({
            "type": "text.done",
            "turn_id": "turn_test",
        })
        yield parse_stream_event({
            "type": "turn.completed",
            "turn_id": "turn_test",
        })

    result, host = await _run_stream(
        monkeypatch,
        [],
        stream_factory=overlapping_retry_stream,
    )

    assert result.status == "completed"
    assert result.assistant_text == "answer"
    surface_id = host.output_session.context.surface_id
    assert [
        item
        for item in host.output_session.activity.items
        if isinstance(item, RetryChanged)
    ] == [
        RetryChanged(
            surface_id=surface_id,
            turn_id="turn_test",
            source="provider",
            state="started",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ),
        RetryChanged(
            surface_id=surface_id,
            turn_id="turn_test",
            source="transport",
            state="started",
            presentation_epoch=1,
            round=1,
            attempt=1,
        ),
        RetryChanged(
            surface_id=surface_id,
            turn_id="turn_test",
            source="transport",
            state="completed",
            presentation_epoch=1,
            round=1,
            attempt=1,
        ),
        RetryChanged(
            surface_id=surface_id,
            turn_id="turn_test",
            source="provider",
            state="completed",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ),
    ]


@pytest.mark.anyio
async def test_stream_preserves_reconciliation_required_without_normal_failure(
    monkeypatch,
) -> None:
    result, host = await _run_stream(monkeypatch, [{
        "proto": "mind.chat",
        "type": "turn.reconciliation_required",
        "turn_id": "turn_test",
        "presentation_epoch": 1,
        "status": "reconciliation_required",
        "effect_id": "effect_uncertain",
        "error": "provider succeeded but commit failed",
    }])

    assert result.status == "reconciliation_required"
    assert result.error == "provider succeeded but commit failed"
    assert host.remembered == []
    assert host.transcripts.entries[-1] == {
        "event": "turn.reconciliation_required",
        "actor": "system",
        "payload": {
            "status": "reconciliation_required",
            "usage": {},
            "error": "provider succeeded but commit failed",
        },
    }
    failure_views = [
        item
        for item in host.output_session.presentation.items
        if isinstance(item, FailureView)
    ]
    assert failure_views
    assert failure_views[-1].phase == "turn.reconciliation_required"
    stream.interrupt_turn.assert_awaited_once()


@pytest.mark.anyio
async def test_stream_auto_reconciles_known_effect_and_completes_new_attempt(
    monkeypatch,
) -> None:
    reconciled_effects = []

    async def reconcile_known_effect(_runner, effect_id):
        reconciled_effects.append(effect_id)
        return True

    monkeypatch.setattr(
        stream.ClientToolCallRunner,
        "reconcile_known_effect",
        reconcile_known_effect,
    )

    result, host = await _run_stream(monkeypatch, [
        {
            "proto": "mind.chat",
            "type": "turn.reconciliation_required",
            "turn_id": "turn_test",
            "presentation_epoch": 1,
            "effect_id": "effect_known",
            "error": "effect ledger commit result is unknown",
        },
        {
            "proto": "mind.chat",
            "type": "presentation.superseded",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
            "superseded_epoch": 1,
        },
        {
            "proto": "mind.chat",
            "type": "turn.started",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
        },
        {
            "proto": "mind.chat",
            "type": "text.delta",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
            "segment_id": "attempt-2:assistant:1",
            "text": "recovered answer",
        },
        {
            "proto": "mind.chat",
            "type": "text.done",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
            "segment_id": "attempt-2:assistant:1",
        },
        {
            "proto": "mind.chat",
            "type": "turn.completed",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
            "status": "completed",
        },
    ])

    assert result.status == "completed"
    assert result.assistant_text == "recovered answer"
    assert reconciled_effects == ["effect_known"]
    stream.interrupt_turn.assert_not_awaited()
    assert not any(
        isinstance(item, FailureView)
        and item.phase == "turn.reconciliation_required"
        for item in host.output_session.presentation.items
    )
    assert PresentationSuperseded(
        surface_id=host.output_session.context.surface_id,
        turn_id="turn_test",
        superseded_epoch=1,
        presentation_epoch=2,
    ) in host.output_session.activity.items


@pytest.mark.anyio
async def test_done_projects_terminal_before_logical_settlement(monkeypatch) -> None:
    stream_advanced = asyncio.Event()
    host = _host()

    async def pending_stream(*_args, **_kwargs):
        yield parse_stream_event({"type": "turn.completed"})
        stream_advanced.set()
        await asyncio.Future()

    task = asyncio.create_task(_run_stream(
        monkeypatch,
        [],
        stream_factory=pending_stream,
        host_state=host,
    ))
    await stream_advanced.wait()

    assert TurnTerminal(
        surface_id=host.output_session.context.surface_id,
        turn_id="turn_test",
        status="completed",
    ) in host.output_session.activity.items

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_stream_forwards_interrupted_completion(
    monkeypatch,
) -> None:
    input_events = []

    result, host = await _run_stream(
        monkeypatch,
        [
            {
                "type": "turn.completed",
                "turn_id": "turn_test",
                "status": "interrupted",
            },
        ],
        on_turn_input_event=input_events.append,
    )

    assert result.status == "interrupted"
    assert len(input_events) == 1
    assert input_events[0].type == "turn.completed"
    assert input_events[0].status == "interrupted"
    assert host.transcripts.entries[-1]["event"] == "turn.interrupted"


@pytest.mark.anyio
async def test_stream_reports_transport_end_after_processing_completion(
    monkeypatch,
) -> None:
    completed = parse_stream_event({
        "type": "turn.completed",
        "turn_id": "turn_test",
    })
    processed = []
    stream_ends = []

    class SettledStream(object):
        end_reason = "settled"

        async def _events(self):
            yield completed

        def __aiter__(self):
            return self._events()

    def open_stream(*_args, **_kwargs):
        return SettledStream()

    result, _host = await _run_stream(
        monkeypatch,
        [],
        stream_factory=open_stream,
        on_turn_input_event=lambda event: processed.append(event.type),
        on_turn_stream_end=stream_ends.append,
    )

    assert result.status == "completed"
    assert processed == ["turn.completed"]
    assert stream_ends == ["settled"]


@pytest.mark.anyio
async def test_stream_projects_one_terminal_activity(
    monkeypatch,
) -> None:
    """验证单一权威终态只投影一个 TurnTerminal 活动事实。"""
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "turn.completed",
            "turn_id": "turn_test",
            "status": "completed",
        },
    ])

    surface_id = host.output_session.context.surface_id
    terminal_events = [
        item
        for item in host.output_session.activity.items
        if isinstance(item, TurnTerminal)
    ]
    assert result.status == "completed"
    assert terminal_events == [
        TurnTerminal(
            surface_id=surface_id,
            turn_id="turn_test",
            status="completed",
        ),
    ]


@pytest.mark.anyio
async def test_internal_stream_gap_waits_for_terminal_without_blocking_next_turn(
    monkeypatch,
) -> None:
    """验证权威内部缺口保留失败事实，并等待真正终态释放执行门。"""
    result, host = await _run_stream(monkeypatch, [
        {
            "type": "stream.gap",
            "gap_kind": "internal",
            "requested_after_seq": 3,
            "expected_event_seq": 4,
            "observed_event_seq": 6,
            "retryable": True,
        },
        {
            "type": "turn.completed",
            "turn_id": "turn_test",
            "status": "completed",
        },
    ])

    surface_id = host.output_session.context.surface_id
    scoped_events = [
        item
        for item in host.output_session.activity.items
        if isinstance(item, (RecoveryChanged, TurnTerminal))
    ]
    assert result.status == "incomplete"
    assert result.error_code == "stream_gap_internal"
    assert scoped_events == [
        RecoveryChanged(
            surface_id=surface_id,
            turn_id="turn_test",
            mode="gap",
            event_seq=4,
        ),
        TurnTerminal(
            surface_id=surface_id,
            turn_id="turn_test",
            status="failed",
        ),
    ]


@pytest.mark.anyio
async def test_turn_start_opens_the_control_event_boundary(monkeypatch) -> None:
    input_events = []

    result, _host = await _run_stream(
        monkeypatch,
        [
            {"type": "turn.started", "turn_id": "turn_test"},
            {"type": "turn.completed", "turn_id": "turn_test"},
        ],
        on_turn_input_event=input_events.append,
    )

    assert result.status == "completed"
    assert [event.type for event in input_events] == [
        "turn.started",
        "turn.completed",
    ]
