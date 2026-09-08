# -*- coding: utf-8 -*-

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


def command_review_action(
    command: list[str],
    *,
    reason: str,
) -> dict[str, typing.Any]:
    """构造与正式服务端命令审批信封同构的评审动作。"""
    return {
        "type": "command",
        "environment_id": "workspace-write",
        "command": list(command),
        "cwd": ".",
        "cwd_raw": ".",
        "reason": reason,
        "tty": False,
        "sandbox_permissions": "use_default",
        "additional_permissions": None,
        "proposed_execpolicy_amendment": None,
        "parsed_cmd": [],
    }


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


@pytest.mark.anyio
async def test_sampling_accepted_input_preserves_local_transcript_order(
    monkeypatch,
) -> None:
    accepted = TurnInput(
        client_message_id="message_1",
        text="change direction",
        attachments=({"kind": "image"},),
        extras={"source": "tui"},
    )

    def handle_input(event):
        if isinstance(event, TurnInputAcceptedEvent):
            return accepted
        return None

    _result, host = await _run_stream(
        monkeypatch,
        [
            {
                "type": "text.delta",
                "segment_id": "before-item",
                "text": "before",
            },
            {
                "type": "turn.input.accepted",
                "turn_id": "turn_test",
                "client_message_id": "message_1",
            },
            {
                "type": "text.delta",
                "segment_id": "after-item",
                "text": "after",
            },
            {"type": "text.done", "segment_id": "after-item"},
            {"type": "turn.completed", "turn_id": "turn_test"},
        ],
        on_turn_input_event=handle_input,
    )

    messages = [
        (entry["actor"], entry["payload"])
        for entry in host.transcripts.entries
        if entry["event"] == "message.created"
    ]
    assert messages == [
        ("user", {"content": "hello"}),
        (
            "assistant",
            {
                "content": "before",
                "item_id": "before-item",
                "presentation_epoch": 1,
                "round": 1,
                "attempt": 1,
            },
        ),
        (
            "user",
            {
                "content": "change direction",
                "attachments": [{"kind": "image"}],
                "extras": {"source": "tui"},
            },
        ),
        (
            "assistant",
            {
                "content": "after",
                "item_id": "after-item",
                "presentation_epoch": 1,
                "round": 1,
                "attempt": 1,
            },
        ),
    ]


@pytest.mark.anyio
async def test_transcript_preserves_assistant_tool_output_order(monkeypatch) -> None:
    _result, host = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "segment_id": "before-item",
            "text": "before",
        },
        {"type": "text.done", "segment_id": "before-item"},
        {
            "type": "tool.output",
            "name": "remote_tool",
            "call_id": "call-1",
            "arguments": {"value": 1},
            "status": "completed",
            "elapsed_ms": 3500,
            "result": {"ok": True, "text": "done"},
        },
        {
            "type": "text.delta",
            "segment_id": "after-item",
            "text": "after",
        },
        {"type": "text.done", "segment_id": "after-item"},
        {"type": "turn.completed", "usage": {}},
    ])

    ordered = [
        (entry["event"], entry["actor"], entry["payload"])
        for entry in host.transcripts.entries
        if entry["actor"] in {"assistant", "tool"}
    ]

    assert ordered[0] == (
        "message.created",
        "assistant",
        {
            "content": "before",
            "item_id": "before-item",
            "presentation_epoch": 1,
            "round": 1,
            "attempt": 1,
        },
    )
    assert ordered[1][0:2] == ("tool.completed", "tool")
    assert ordered[1][2]["duration_ms"] == 3500
    assert ordered[2] == (
        "message.created",
        "assistant",
        {
            "content": "after",
            "item_id": "after-item",
            "presentation_epoch": 1,
            "round": 1,
            "attempt": 1,
        },
    )


@pytest.mark.anyio
async def test_transcript_records_user_replay_payload(monkeypatch) -> None:
    _result, host = await _run_stream(
        monkeypatch,
        [{"type": "turn.completed", "usage": {}}],
        attachments=({"filename": "screen.png"},),
        extras={"selection": "src/app.py"},
    )

    user_entry = next(
        entry
        for entry in host.transcripts.entries
        if entry["actor"] == "user"
    )
    assert user_entry["payload"] == {
        "content": "hello",
        "attachments": [{"filename": "screen.png"}],
        "extras": {"selection": "src/app.py"},
    }


@pytest.mark.anyio
async def test_child_stream_does_not_mutate_root_frontend_state(monkeypatch) -> None:
    result, host = await _run_stream(
        monkeypatch,
        [
            {"type": "text.delta", "text": "child answer"},
            {"type": "turn.completed"},
        ],
        child_agent=True,
        frontend_active=False,
    )

    assert result.status == "completed"
    assert result.assistant_text == "child answer"
    assert host.remembered == []


@pytest.mark.anyio
async def test_child_stream_failure_does_not_stop_root_animation(monkeypatch) -> None:
    async def fail_stream(*_args, **_kwargs):
        raise RuntimeError("child stream failed")
        if False:
            yield None

    result, host = await _run_stream(
        monkeypatch,
        [],
        child_agent=True,
        frontend_active=False,
        stream_factory=fail_stream,
    )

    assert result.status == "failed"


@pytest.mark.anyio
async def test_stream_preserves_explicit_empty_skills(monkeypatch) -> None:
    async def stream_with_no_skills(*_args, **kwargs):
        assert kwargs["skills"] == []
        yield parse_stream_event({"type": "turn.completed"})

    result, _host_state = await _run_stream(
        monkeypatch,
        [],
        request_skills=(),
        stream_factory=stream_with_no_skills,
    )

    assert result.status == "completed"


@pytest.mark.anyio
async def test_stream_forwards_turn_additional_context(monkeypatch) -> None:
    async def stream_with_context(*_args, **kwargs):
        assert kwargs["additional_context"] == [
            "inspect security boundaries",
            "check cancellation paths",
        ]
        yield parse_stream_event({"type": "turn.completed"})

    result, _host_state = await _run_stream(
        monkeypatch,
        [],
        additional_context=(
            "inspect security boundaries",
            "check cancellation paths",
        ),
        stream_factory=stream_with_context,
    )

    assert result.status == "completed"


@pytest.mark.anyio
async def test_stream_forwards_turn_hook_context(monkeypatch) -> None:
    class CommandRunner(object):
        async def execute(self, definition, _payload):
            if definition.event == "SessionStart":
                return HookCommandOutput(data={
                    "systemMessage": "session system",
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "session context",
                    },
                })
            return HookCommandOutput(data={
                "systemMessage": "prompt system",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "prompt context",
                },
            })

    definitions = resolve_hook_definitions(
        {
            "SessionStart": [_hook("start", matcher="startup")],
            "UserPromptSubmit": [_hook("prompt")],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    async def stream_with_context(*_args, **kwargs):
        assert kwargs["additional_context"] == [
            "session context",
            "prompt context",
        ]
        assert "system_message" not in kwargs
        yield parse_stream_event({"type": "turn.completed"})

    result, _host_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        session_started=True,
        stream_factory=stream_with_context,
    )

    assert result.status == "completed"


@pytest.mark.anyio
async def test_exec_output_session_receives_hook_lifecycle_views(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={})

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
        [{"type": "turn.completed"}],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        session_started=True,
        show_hook_lifecycle=True,
    )

    assert result.status == "completed"
    hook_views = [
        item
        for item in host.output_session.presentation.items
        if isinstance(item, HookRunView)
    ]
    assert [(view.event, view.phase, view.status) for view in hook_views] == [
        ("SessionStart", "started", "running"),
        ("SessionStart", "completed", "completed"),
        ("UserPromptSubmit", "started", "running"),
        ("UserPromptSubmit", "completed", "completed"),
        ("Stop", "started", "running"),
        ("Stop", "completed", "completed"),
    ]


@pytest.mark.anyio
async def test_session_start_stop_queues_context_for_next_turn(monkeypatch) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={
                "continue": False,
                "stopReason": "configure first",
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "Python 3.13 is required",
                },
            })

    definitions = resolve_hook_definitions(
        {"SessionStart": [_hook("start", matcher="startup")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, host_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        session_started=True,
    )

    assert result.status == "failed"
    assert result.error == "configure first"
    assert host_state.queued_context == [("Python 3.13 is required",)]


@pytest.mark.anyio
async def test_prompt_stop_queues_context_for_next_turn(monkeypatch) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={
                "continue": False,
                "stopReason": "Select a project first.",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        "Available projects: web, app, service."
                    ),
                },
            })

    definitions = resolve_hook_definitions(
        {"UserPromptSubmit": [_hook("prompt")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, host_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "failed"
    assert result.error == "Select a project first."
    assert host_state.queued_context == [(
        "Available projects: web, app, service.",
    )]


@pytest.mark.anyio
async def test_child_prompt_stop_returns_context_without_queuing_root(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={
                "continue": False,
                "stopReason": "Select a project first.",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        "Available projects: web, app, service."
                    ),
                },
            })

    definitions = resolve_hook_definitions(
        {"UserPromptSubmit": [_hook("prompt")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, host_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        child_agent=True,
    )

    assert result.status == "failed"
    assert result.additional_context == (
        "Available projects: web, app, service.",
    )
    assert host_state.queued_context == []


@pytest.mark.anyio
async def test_stream_runs_turn_hooks_from_one_scope(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
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

    result, host_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.completed", "usage": {"output_tokens": 2}}],
        hooks=HookRuntime(definitions, command_runner=runner),
        session_started=True,
    )

    assert result.status == "completed"
    assert [event for event, _payload in runner.calls] == [
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
    ]
    assert runner.calls[0][1]["source"] == "startup"
    assert runner.calls[1][1]["prompt"] == "hello"
    assert runner.calls[2][1]["stop_hook_active"] is False
    assert runner.calls[2][1]["last_assistant_message"] is None
    assert host_state.transcripts.entries[0] == {
        "event": "session.started",
        "actor": "system",
        "payload": {
            "cwd": ".",
            "source": "test",
            "reason": "initial",
            "model": "",
        },
    }


@pytest.mark.anyio
async def test_child_stream_skips_root_session_and_stop_lifecycle_hooks(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.events = []

        async def execute(self, definition, _payload):
            self.events.append(definition.event)
            if definition.event == "SessionStart":
                return HookCommandOutput(data={
                    "continue": False,
                    "stopReason": "root startup policy",
                })
            if definition.event == "Stop":
                return HookCommandOutput(data={
                    "decision": "block",
                    "reason": "root continuation",
                })
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

    result, _host_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.completed"}],
        hooks=HookRuntime(definitions, command_runner=runner),
        session_started=True,
        child_agent=True,
    )

    assert result.status == "completed"
    assert runner.events == ["UserPromptSubmit"]


@pytest.mark.anyio
async def test_stream_reuses_injected_hook_scope_snapshot(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.events = []

        async def execute(self, definition, _payload):
            self.events.append(definition.event)
            return HookCommandOutput(data={})

    injected_runner = CommandRunner()
    resolved_runner = CommandRunner()
    injected_definitions = resolve_hook_definitions(
        {
            "UserPromptSubmit": [_hook("injected-prompt")],
            "Stop": [_hook("injected-stop")],
        },
        source_scope="user",
        source_path=Path("injected.toml"),
    )
    resolved_definitions = resolve_hook_definitions(
        {
            "UserPromptSubmit": [_hook("resolved-prompt")],
            "Stop": [_hook("resolved-stop")],
        },
        source_scope="user",
        source_path=Path("resolved.toml"),
    )
    injected_runtime = HookRuntime(
        injected_definitions,
        command_runner=injected_runner,
    )
    resolved_runtime = HookRuntime(
        resolved_definitions,
        command_runner=resolved_runner,
    )

    result, host = await _run_stream(
        monkeypatch,
        [{"type": "turn.completed"}],
        hooks=resolved_runtime,
        hook_scope_factory=lambda context: HookExecutionScope(
            context=context,
            dispatcher=injected_runtime,
        ),
    )

    assert result.status == "completed"
    assert injected_runner.events == ["UserPromptSubmit", "Stop"]
    assert resolved_runner.events == []
    assert not hasattr(host, "hook_scope")


@pytest.mark.anyio
async def test_prompt_hook_denial_skips_stop_and_continuation(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            if definition.event == "UserPromptSubmit":
                return HookCommandOutput(data={
                    "continue": False,
                    "stopReason": "prompt blocked",
                })
            return HookCommandOutput(data={
                "decision": "block",
                "reason": "retry blocked prompt",
            })

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "UserPromptSubmit": [_hook("prompt")],
            "Stop": [_hook("stop")],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, _host_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.completed"}],
        hooks=HookRuntime(definitions, command_runner=runner),
    )

    assert result.status == "failed"
    assert result.error == "prompt blocked"
    assert [event for event, _payload in runner.calls] == ["UserPromptSubmit"]


@pytest.mark.anyio
async def test_stop_hook_failure_does_not_replace_completed_result(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, definition, _payload):
            if definition.event == "Stop":
                raise RuntimeError("stop hook failed")
            return HookCommandOutput(data={})

    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, _host_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.completed", "usage": {"output_tokens": 2}}],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result == RunResult(
        status="completed",
        usage={"output_tokens": 2},
    )


@pytest.mark.anyio
async def test_stream_cancellation_reports_interrupted_stop_hook(
    monkeypatch,
) -> None:
    started = asyncio.Event()

    async def pending_stream(*_args, **_kwargs):
        started.set()
        await asyncio.Future()
        if False:
            yield None

    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            return HookCommandOutput(data={
                "decision": "block",
                "reason": "should be ignored",
            })

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    task = asyncio.create_task(_run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=runner),
        stream_factory=pending_stream,
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [event for event, _payload in runner.calls] == ["Stop"]
    assert runner.calls[0][1]["stop_hook_active"] is False


@pytest.mark.anyio
async def test_stop_hook_continuation_runs_another_turn(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.payloads = []

        async def execute(self, _definition, payload):
            self.payloads.append(payload)
            if not payload["stop_hook_active"]:
                return HookCommandOutput(data={
                    "decision": "block",
                    "reason": "continue once",
                    "systemMessage": "stop system",
                })
            return HookCommandOutput(data={})

    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    runner = CommandRunner()
    messages = []
    request_kwargs = []

    async def stream_with_continuation(_pref, message, _tools, **kwargs):
        messages.append(message)
        request_kwargs.append(kwargs)
        yield parse_stream_event({
            "type": "text.delta",
            "turn_id": kwargs["turn_id"],
            "text": f"reply {len(messages)}",
        })
        yield parse_stream_event({
            "type": "turn.completed",
            "turn_id": kwargs["turn_id"],
        })

    result, _host_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=runner),
        stream_factory=stream_with_continuation,
    )

    assert result.status == "completed"
    assert result.assistant_text == "reply 2"
    assert messages == ["hello", "continue once"]
    assert request_kwargs[0]["turn_id"] != request_kwargs[1]["turn_id"]
    assert "additional_context" not in request_kwargs[1]
    assert "system_message" not in request_kwargs[1]
    assert [payload["stop_hook_active"] for payload in runner.payloads] == [
        False,
        True,
    ]


@pytest.mark.anyio
async def test_completed_turn_allows_stop_hook_continuation(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, payload):
            if not payload["stop_hook_active"]:
                return HookCommandOutput(data={
                    "decision": "block",
                    "reason": "continue once",
                })
            return HookCommandOutput(data={})

    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    messages = []

    async def terminal_stream(_pref, message, _tools, **kwargs):
        messages.append(message)
        if len(messages) == 1:
            yield parse_stream_event({
                "type": "text.delta",
                "turn_id": kwargs["turn_id"],
                "text": "partial answer",
            })
            yield parse_stream_event({
                "type": "turn.completed",
                "turn_id": kwargs["turn_id"],
                "status": "completed",
                "response_id": "msg_1",
                "route": "messages",
                "usage": {"output_tokens": 7},
                "stop_reason": "max_tokens",
            })
            return
        yield parse_stream_event({
            "type": "turn.completed",
            "turn_id": kwargs["turn_id"],
            "status": "completed",
        })

    result, host = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        stream_factory=terminal_stream,
    )

    assert result.status == "completed"
    assert messages == ["hello", "continue once"]
    assert host.transcripts.entries[-1]["event"] == "turn.completed"


@pytest.mark.anyio
async def test_pause_turn_failure_does_not_run_stop_hook_continuation(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={
                "decision": "block",
                "reason": "retry",
            })

    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    messages = []

    async def failed_stream(_pref, message, _tools, **_kwargs):
        messages.append(message)
        yield parse_stream_event({
            "type": "turn.completed",
            "status": "failed",
            "error": "pause_turn is not supported",
            "route": "messages",
            "usage": {"output_tokens": 2},
            "stop_reason": "pause_turn",
        })

    result, host = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        stream_factory=failed_stream,
    )

    assert result.status == "failed"
    assert result.error == "pause_turn is not supported"
    assert result.stop_reason == "pause_turn"
    assert result.usage == {"output_tokens": 2}
    assert messages == ["hello"]
    assert host.transcripts.entries[-1]["payload"]["stop_reason"] == (
        "pause_turn"
    )
    failure_view = host.output_session.presentation.items[-1]
    assert isinstance(failure_view, FailureView)
    assert failure_view.error == "pause_turn is not supported"
    assert failure_view.stop_reason == "pause_turn"
    assert failure_view.usage == {"output_tokens": 2}


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
    ])

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
    ])

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
    )

    assert result.status == "failed"
    assert result.additional_context == ("inspect protected paths",)
    assert host_state.queued_context == [("inspect protected paths",)]


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


@pytest.mark.anyio
async def test_stream_registers_review_before_approval_core_decides(
    monkeypatch,
) -> None:
    approval_posts = []
    executions = []
    result_posts = []
    review_action = command_review_action(
        ["echo reviewed"],
        reason="Run the requested command.",
    )

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        executions.append(invocation)
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

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    async def post_tool_result(*args, **kwargs):
        result_posts.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    host = _host()
    coordinator = DomainApprovalCoordinator(
        host.approval_coordinator,
        fact_store=InMemoryApprovalFactStore(),
        grant_store=InMemorySessionGrantStore(),
    )
    host.approval_coordinator = coordinator

    result, host_state = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.approval_review.started",
                "event_seq": 1,
                "review_id": "review-stream",
                "approval_id": "approval-stream",
                "call_id": "call-stream",
                "target_item_id": "call-stream",
                "kind": "command",
                "action": dict(review_action),
                "started_at_ms": 100,
                "review": {"status": "in_progress"},
            },
            {
                "type": "tool.approval_review.completed",
                "event_seq": 2,
                "review_id": "review-stream",
                "approval_id": "approval-stream",
                "call_id": "call-stream",
                "target_item_id": "call-stream",
                "kind": "command",
                "action": dict(review_action),
                "started_at_ms": 100,
                "completed_at_ms": 125,
                "decision_source": "agent",
                "review": {
                    "status": "approved",
                    "risk_level": "low",
                    "user_authorization": "high",
                    "rationale": "The command matches the request.",
                },
            },
            {
                "type": "tool.approval_required",
                "event_seq": 3,
                "call_id": "call-stream",
                "approval_id": "approval-stream",
                "kind": "command",
                "command": ["echo reviewed"],
                "cwd": ".",
                "reason": "Run the requested command.",
            },
            {
                "type": "tool.call",
                "event_seq": 4,
                "call_id": "call-stream",
                "name": "exec_command",
                "arguments": {"command": "echo reviewed"},
            },
            {
                "type": "turn.completed",
                "event_seq": 5,
                "status": "completed",
            },
        ],
        host_state=host,
    )

    assert result.status == "completed"
    host_state.frontend.interaction.present_approval.assert_not_awaited()
    assert len(approval_posts) == 1
    assert approval_posts[0][0][:4] == (
        "cid_test",
        "sid_test",
        "call-stream",
        "approval-stream",
    )
    assert approval_posts[0][1]["decision"] == "accept"
    assert len(executions) == 1
    assert len(result_posts) == 1
    assert result_posts[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call-stream",
        "exec_command",
        True,
    )
    review_views = [
        item
        for item in host_state.output_session.presentation.items
        if isinstance(item, ApprovalReviewView)
    ]
    assert len(review_views) == 1
    assert review_views[0].status == "approved"

    await coordinator.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "review_status",
    ("denied", "timed_out"),
)
async def test_stream_preserves_auto_review_failure_status(
    monkeypatch,
    review_status: str,
) -> None:
    approval_posts = []
    reason = "Run the requested command."

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)
    host = _host()
    coordinator = DomainApprovalCoordinator(
        host.approval_coordinator,
        fact_store=InMemoryApprovalFactStore(),
        grant_store=InMemorySessionGrantStore(),
    )
    host.approval_coordinator = coordinator
    review = (
        {
            "status": "denied",
            "risk_level": "high",
            "user_authorization": "low",
            "rationale": "The command exceeds the requested scope.",
        }
        if review_status == "denied"
        else {
            "status": "timed_out",
            "rationale": "The automatic review exceeded its budget.",
        }
    )

    result, host_state = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.approval_review.completed",
                "event_seq": 1,
                "review_id": f"review-{review_status}",
                "approval_id": f"approval-{review_status}",
                "call_id": f"call-{review_status}",
                "target_item_id": f"call-{review_status}",
                "kind": "command",
                "action": command_review_action(
                    ["echo", "reviewed"],
                    reason=reason,
                ),
                "started_at_ms": 100,
                "completed_at_ms": 125,
                "decision_source": "agent",
                "review": review,
            },
            {
                "type": "tool.approval_required",
                "event_seq": 2,
                "call_id": f"call-{review_status}",
                "approval_id": f"approval-{review_status}",
                "kind": "command",
                "command": ["echo", "reviewed"],
                "cwd": ".",
                "reason": reason,
            },
            {
                "type": "tool.approval_required",
                "event_seq": 3,
                "call_id": f"call-{review_status}",
                "approval_id": f"approval-{review_status}",
                "kind": "command",
                "command": ["echo", "reviewed"],
                "cwd": ".",
                "reason": reason,
            },
            {"type": "turn.completed", "event_seq": 4},
        ],
        host_state=host,
    )

    assert result.status == "completed"
    host_state.frontend.interaction.present_approval.assert_not_awaited()
    assert len(approval_posts) == 1
    assert approval_posts[0][1]["decision"] == "decline"
    review_views = [
        item
        for item in host_state.output_session.presentation.items
        if isinstance(item, ApprovalReviewView)
    ]
    assert len(review_views) == 1
    assert review_views[0].status == review_status

    await coordinator.close()


@pytest.mark.anyio
async def test_permission_hook_precedes_received_auto_review_decision(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow"},
                },
            })

    reason = "Run the requested command."
    definitions = resolve_hook_definitions(
        {
            "PermissionRequest": [{
                "hooks": [{"type": "command", "command": "review"}],
                "matcher": "exec_command",
            }],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )
    approval_posts = []

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)
    host = _host()
    coordinator = DomainApprovalCoordinator(
        host.approval_coordinator,
        fact_store=InMemoryApprovalFactStore(),
        grant_store=InMemorySessionGrantStore(),
    )
    host.approval_coordinator = coordinator

    result, host_state = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.approval_review.completed",
                "event_seq": 1,
                "review_id": "review-hook-precedence",
                "approval_id": "approval-hook-precedence",
                "call_id": "call-hook-precedence",
                "target_item_id": "call-hook-precedence",
                "kind": "command",
                "action": command_review_action(
                    ["echo", "reviewed"],
                    reason=reason,
                ),
                "started_at_ms": 100,
                "completed_at_ms": 125,
                "decision_source": "agent",
                "review": {
                    "status": "denied",
                    "risk_level": "high",
                    "user_authorization": "low",
                    "rationale": "The automatic reviewer denied the command.",
                },
            },
            {
                "type": "tool.approval_required",
                "event_seq": 2,
                "call_id": "call-hook-precedence",
                "approval_id": "approval-hook-precedence",
                "kind": "command",
                "command": ["echo", "reviewed"],
                "cwd": ".",
                "reason": reason,
            },
            {"type": "turn.completed", "event_seq": 3},
        ],
        host_state=host,
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "completed"
    host_state.frontend.interaction.present_approval.assert_not_awaited()
    assert approval_posts[0][1]["decision"] == "accept"
    assert not any(
        isinstance(item, ApprovalReviewView)
        for item in host_state.output_session.presentation.items
    )

    await coordinator.close()


@pytest.mark.anyio
async def test_stream_rejects_review_for_different_approval_action(
    monkeypatch,
) -> None:
    approval_posts = []
    review_action = command_review_action(
        ["echo", "reviewed"],
        reason="Run a different command.",
    )

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)
    host = _host()
    coordinator = DomainApprovalCoordinator(
        host.approval_coordinator,
        fact_store=InMemoryApprovalFactStore(),
        grant_store=InMemorySessionGrantStore(),
    )
    host.approval_coordinator = coordinator

    result, host_state = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.approval_review.completed",
                "event_seq": 1,
                "review_id": "review-mismatch",
                "approval_id": "approval-mismatch",
                "call_id": "call-mismatch",
                "target_item_id": "call-mismatch",
                "kind": "command",
                "action": review_action,
                "started_at_ms": 100,
                "completed_at_ms": 125,
                "decision_source": "agent",
                "review": {
                    "status": "approved",
                    "risk_level": "low",
                    "user_authorization": "high",
                    "rationale": "The reviewed command matches the request.",
                },
            },
            {
                "type": "tool.approval_required",
                "event_seq": 2,
                "call_id": "call-mismatch",
                "approval_id": "approval-mismatch",
                "kind": "command",
                "command": ["echo", "changed"],
                "cwd": ".",
                "reason": "Run a different command.",
            },
        ],
        host_state=host,
    )

    assert result.status == "failed"
    assert result.error == (
        "ApprovalReviewConflict: approval review action does not match request"
    )
    host_state.frontend.interaction.present_approval.assert_not_awaited()
    assert approval_posts == []

    await coordinator.close()


@pytest.mark.anyio
async def test_child_approval_uses_local_agent_identity(monkeypatch) -> None:
    approval_posts = []

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, host = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.approval_required",
                "call_id": "call-child",
                "approval_id": "approval-child",
                "kind": "command",
                "command": "pytest -q",
                "cwd": ".",
                "reason": "模型需要运行测试。",
            },
            {"type": "turn.completed"},
        ],
        child_agent=True,
    )

    assert result.status == "completed"
    request = host.frontend.interaction.present_approval.await_args.args[0]
    presentation = request.presentation
    assert presentation.context.agent_id == "agent_child"
    assert presentation.context.agent_type == "worker"
    assert presentation.context.agent_depth == 1
    assert approval_posts[0][0][:4] == (
        "cid_test",
        "sid_test",
        "call-child",
        "approval-child",
    )


@pytest.mark.anyio
async def test_approval_request_event_is_presented_without_nested_metadata(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return HookCommandOutput(data={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "unsafe operation",
                    "additionalContext": "Use the safe tool instead.",
                },
            })

    definitions = resolve_hook_definitions(
        _durable_tool_call({
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "check"}],
                "matcher": "test_tool",
            }],
        }),
        source_scope="user",
        source_path=Path("config.toml"),
    )
    approval_posts = []

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, host = await _run_stream(
        monkeypatch,
        [{
            "type": "tool.approval_required",
            "call_id": "call-denied",
            "approval_id": "approval-denied",
            "kind": "command",
            "command": "pytest -q",
            "cwd": ".",
            "reason": "模型需要运行测试。",
        }, {"type": "turn.completed"}],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "completed"
    host.frontend.interaction.present_approval.assert_awaited_once()
    approval_kwargs = dict(approval_posts[0][1])
    assert approval_kwargs.pop("turn_id")
    assert approval_kwargs.pop("kind") == "command"
    assert approval_kwargs.pop("approval")["kind"] == "command"
    assert approval_kwargs == {
        "decision": "accept",
        "reason": None,
    }


@pytest.mark.anyio
async def test_stream_uses_typed_approval_before_client_tool_call(monkeypatch) -> None:
    approval_posts = []
    result_posts = []

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

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    async def post_tool_result(*args, **kwargs):
        result_posts.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, host = await _run_stream(monkeypatch, [
        {
            "type": "tool.approval_required",
            "call_id": "call-approved",
            "approval_id": "approval-1",
            "kind": "command",
            "command": "pytest -q",
            "cwd": ".",
            "reason": "模型需要运行测试。",
        },
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-approved",
            "name": "test_tool",
            "arguments": {"value": 1},
        }),
        {"type": "turn.completed"},
    ])

    assert result.status == "completed"
    host.frontend.interaction.present_approval.assert_awaited_once()
    assert approval_posts[0][0] == (
        "cid_test",
        "sid_test",
        "call-approved",
        "approval-1",
    )
    approval_kwargs = dict(approval_posts[0][1])
    assert approval_kwargs.pop("turn_id")
    assert approval_kwargs.pop("kind") == "command"
    assert approval_kwargs.pop("approval")["kind"] == "command"
    assert approval_kwargs == {
        "decision": "accept",
        "reason": None,
    }
    assert result_posts[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call-approved",
        "test_tool",
        True,
    )


@pytest.mark.anyio
async def test_confirmed_approval_skips_duplicate_local_prompt_on_replayed_call(
    monkeypatch,
) -> None:
    executions = []
    result_posts = []

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        executions.append(invocation)
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
        result_posts.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    monkeypatch.setattr(stream, "post_tool_approval", AsyncMock())

    result, host = await _run_stream(monkeypatch, [
        {
            "type": "tool.approval_required",
            "call_id": "call-shell-approved",
            "approval_id": "approval-shell-approved",
            "kind": "command",
            "command": "rm -rf build",
            "cwd": ".",
            "reason": "模型需要清理构建目录。",
            "available_decisions": ["accept", "decline"],
        },
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-shell-approved",
            "name": "shell_command",
            "arguments": {"command": "rm -rf build"},
            "reason": "模型需要清理构建目录。",
        }),
        {"type": "turn.completed"},
    ])

    assert result.status == "completed"
    assert len(executions) == 1
    assert len(result_posts) == 1
    host.frontend.interaction.present_approval.assert_awaited_once()


@pytest.mark.anyio
async def test_reviewer_approval_cannot_override_local_forbidden_rule(
    monkeypatch,
) -> None:
    executions = []
    approval_posts = []
    result_posts = []
    host = _host()
    host.workspace_runtime.execution_policy.policy = Policy.from_parts([
        PrefixRule(
            PrefixPattern.from_values(["rm"]),
            decision=Decision.Forbidden,
            justification="本地规则禁止删除命令。",
        ),
    ])

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        executions.append(invocation)
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="must not execute",
                call_id=invocation.call_id,
                fields=_client_result_fields(
                    invocation,
                    text="must not execute",
                ),
            )
        )

    async def post_tool_result(*args, **kwargs):
        result_posts.append((args, kwargs))
        return {}

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    coordinator = DomainApprovalCoordinator(
        host.approval_coordinator,
        fact_store=InMemoryApprovalFactStore(),
        grant_store=InMemorySessionGrantStore(),
    )
    host.approval_coordinator = coordinator
    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, _ = await _run_stream(monkeypatch, [
        {
            "type": "tool.approval_review.completed",
            "event_seq": 1,
            "review_id": "review-forbidden-after-remote-approval",
            "approval_id": "approval-forbidden-after-remote-approval",
            "call_id": "call-forbidden-after-remote-approval",
            "target_item_id": "call-forbidden-after-remote-approval",
            "kind": "command",
            "action": command_review_action(
                ["rm -rf build"],
                reason="模型需要清理构建目录。",
            ),
            "started_at_ms": 100,
            "completed_at_ms": 125,
            "decision_source": "agent",
            "review": {
                "status": "approved",
                "risk_level": "low",
                "user_authorization": "high",
                "rationale": "The command matches the request.",
            },
        },
        {
            "type": "tool.approval_required",
            "call_id": "call-forbidden-after-remote-approval",
            "approval_id": "approval-forbidden-after-remote-approval",
            "kind": "command",
            "command": "rm -rf build",
            "cwd": ".",
            "reason": "模型需要清理构建目录。",
            "available_decisions": ["accept", "decline"],
        },
        {"type": "turn.completed"},
    ], host_state=host)

    assert result.status == "completed"
    host.frontend.interaction.present_approval.assert_not_awaited()
    assert len(approval_posts) == 1
    assert approval_posts[0][1]["decision"] == "decline"
    assert approval_posts[0][1]["reason"] == "本地规则禁止删除命令。"
    assert executions == []
    assert result_posts == []

    await coordinator.close()


@pytest.mark.anyio
async def test_local_policy_cancel_interrupts_turn(monkeypatch) -> None:
    result_posts = []

    async def request_outcome(_coordinator, _approval):
        return ApprovalOutcome.create(
            "cancel",
            source="user",
            reason="user",
        )

    async def post_tool_result(*args, **kwargs):
        result_posts.append((args, kwargs))
        return {}

    monkeypatch.setattr(ApprovalCoordinator, "request_outcome", request_outcome)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _host_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-local-cancel",
            "name": "shell_command",
            "arguments": {"command": "rm -rf build"},
            "reason": "模型需要清理构建目录。",
        }),
    ])

    assert result.status == "interrupted"
    assert result_posts[0][0][4] is False
    assert result_posts[0][0][5] == {
        "ok": False,
        "tool": "shell_command",
        "source": "client",
        "args": {"command": "rm -rf build"},
        "text": "user cancelled",
        "attachments": [],
        "data": {
            "executed": False,
            "status": "cancelled",
        },
    }
    stream.interrupt_turn.assert_awaited_once()


@pytest.mark.anyio
async def test_stream_persists_local_shell_rule_from_approval(
    monkeypatch,
) -> None:
    result_posts = []

    async def request_outcome(_coordinator, approval):
        assert approval["reason"] == "清理临时构建目录。"
        assert "justification" not in approval
        assert approval["proposed_execpolicy_amendment"]["display"] == "rm -rf"
        return ApprovalOutcome.create(
            "acceptWithExecpolicyAmendment",
            source="user",
            reason="user",
        )

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

    async def post_tool_result(*args, **kwargs):
        result_posts.append((args, kwargs))
        return {}

    monkeypatch.setattr(
        ApprovalCoordinator,
        "request_outcome",
        request_outcome,
    )
    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _host_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-amendment",
            "name": "shell_command",
            "arguments": {"command": "rm -rf build"},
            "reason": "清理临时构建目录。",
        }),
        {"type": "turn.completed"},
    ])

    assert result.status == "completed"
    assert result_posts[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call-amendment",
        "shell_command",
        True,
    )


@pytest.mark.anyio
async def test_stream_allows_local_shell_approval_without_tool_reason(
    monkeypatch,
) -> None:
    approvals = []
    executions = []

    async def request_outcome(_coordinator, approval):
        approvals.append(approval)
        return ApprovalOutcome.create(
            "accept",
            source="user",
            reason="user",
        )

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        executions.append(invocation)
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

    monkeypatch.setattr(
        ApprovalCoordinator,
        "request_outcome",
        request_outcome,
    )
    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", AsyncMock(return_value={}))

    result, _host_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-without-reason",
            "name": "shell_command",
            "arguments": {"command": "rm -rf build"},
        }),
        {"type": "turn.completed"},
    ])

    assert result.status == "completed"
    assert len(approvals) == 1
    assert approvals[0]["reason"]
    assert len(executions) == 1


@pytest.mark.anyio
async def test_local_patch_session_approval_skips_next_matching_patch(
    monkeypatch,
) -> None:
    approval_calls = []
    executions = []

    async def request_outcome(_coordinator, approval):
        approval_calls.append(approval)
        return ApprovalOutcome.create(
            "acceptForSession",
            source="user",
            reason="user",
        )

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        executions.append(invocation)
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

    monkeypatch.setattr(
        ApprovalCoordinator,
        "request_outcome",
        request_outcome,
    )
    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", AsyncMock(return_value={}))

    host = _host()
    host.workspace_runtime.coding = SimpleNamespace(
        preview_patch=lambda **_kwargs: {
            "ok": True,
            "data": {"files": [{"path": "src/app.py"}]},
        },
    )
    patch_event = _durable_tool_call({
        "type": "tool.call",
        "call_id": "call-local-patch",
        "name": "apply_patch",
        "arguments": {"patch": "*** Begin Patch\n*** End Patch"},
    })
    untrusted = PermissionSettings("workspace-write", "untrusted")

    first, _ = await _run_stream(
        monkeypatch,
        [patch_event, {"type": "turn.completed"}],
        host_state=host,
        permissions=untrusted,
    )
    second, _ = await _run_stream(
        monkeypatch,
        [patch_event, {"type": "turn.completed"}],
        host_state=host,
        permissions=untrusted,
    )

    assert first.status == "completed"
    assert second.status == "completed"
    assert len(approval_calls) == 1
    assert len(executions) == 2


@pytest.mark.anyio
async def test_declined_tool_closes_without_interrupting_turn(monkeypatch) -> None:
    approval_posts = []

    async def request_outcome(_coordinator, _approval):
        return ApprovalOutcome.create(
            "decline",
            source="user",
            reason="user",
        )

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(
        ApprovalCoordinator,
        "request_outcome",
        request_outcome,
    )
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, host = await _run_stream(monkeypatch, [
        {
            "type": "tool.approval_required",
            "call_id": "call-declined",
            "approval_id": "approval-declined",
            "kind": "command",
            "command": "pytest -q",
            "cwd": ".",
            "reason": "模型需要运行测试。",
        },
        {
            "type": "tool.output",
            "call_id": "call-declined",
            "name": "test_tool",
            "status": "declined",
            "ok": False,
            "result": {"ok": False, "text": "user denied"},
        },
        {"type": "text.delta", "text": "我会换一种方式。"},
        {"type": "text.done"},
        {"type": "turn.completed", "status": "completed"},
    ])

    assert result.status == "completed"
    assert result.assistant_text == "我会换一种方式。"
    assert approval_posts[0][1]["decision"] == "decline"
    tool_entry = next(
        entry
        for entry in host.transcripts.entries
        if entry["event"] == "tool.completed"
    )
    assert tool_entry["payload"]["status"] == "declined"
    approval_views = [
        item
        for item in host.output_session.presentation.items
        if isinstance(item, ApprovalView)
    ]
    assert [view.decision for view in approval_views] == ["decline"]
    assert len(host.output_session.presentation.items) == 3


@pytest.mark.anyio
async def test_noninteractive_approval_decline_is_attributed_to_policy(
    monkeypatch,
) -> None:
    monkeypatch.setattr(stream, "post_tool_approval", AsyncMock())
    host_state = _host(frontend_active=False)
    host_state.approval_coordinator = ApprovalCoordinator(
        NonInteractiveInteraction()
    )

    result, host = await _run_stream(
        monkeypatch,
        [
            _durable_tool_call({
                "type": "tool.approval_required",
                "call_id": "call-policy",
                "approval_id": "approval-policy",
                "kind": "command",
                "command": "pytest -q",
                "cwd": ".",
                "reason": "模型需要运行测试。",
            }),
            {"type": "turn.completed", "status": "completed"},
        ],
        frontend_active=False,
        host_state=host_state,
    )

    assert result.status == "completed"
    approval_view = next(
        item
        for item in host.output_session.presentation.items
        if isinstance(item, ApprovalView)
    )
    assert approval_view.decision == "decline"
    assert approval_view.source == "policy"


@pytest.mark.anyio
async def test_cancelled_approval_drains_interrupted_turn_settlement(
    monkeypatch,
) -> None:
    approval_posts = []
    input_events = []

    async def request_outcome(_coordinator, _approval):
        return ApprovalOutcome.create(
            "cancel",
            source="user",
            reason="batch_cancelled",
        )

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(
        ApprovalCoordinator,
        "request_outcome",
        request_outcome,
    )
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, host = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.approval_required",
                "turn_id": "turn_test",
                "call_id": "call-cancelled",
                "approval_id": "approval-cancelled",
                "kind": "command",
                "command": "pytest -q",
                "cwd": ".",
                "reason": "模型需要运行测试。",
            },
            {
                "type": "tool.output",
                "turn_id": "turn_test",
                "call_id": "call-cancelled",
                "name": "test_tool",
                "status": "cancelled",
                "ok": False,
                "result": {"ok": False, "text": "user cancelled"},
            },
            {
                "type": "turn.completed",
                "turn_id": "turn_test",
                "status": "interrupted",
            },
        ],
        on_turn_input_event=input_events.append,
    )

    assert result.status == "interrupted"
    approval_kwargs = dict(approval_posts[0][1])
    assert approval_kwargs.pop("turn_id")
    assert approval_kwargs.pop("kind") == "command"
    assert approval_kwargs.pop("approval")["kind"] == "command"
    assert approval_kwargs == {
        "decision": "cancel",
        "reason": "user cancelled",
    }
    tool_entry = next(
        entry
        for entry in host.transcripts.entries
        if entry["event"] == "tool.completed"
    )
    assert tool_entry["payload"]["status"] == "cancelled"
    approval_views = [
        item
        for item in host.output_session.presentation.items
        if isinstance(item, ApprovalView)
    ]
    assert [view.decision for view in approval_views] == ["cancel"]
    assert len(host.output_session.presentation.items) == 2
    assert len(input_events) == 1
    assert input_events[0].type == "turn.completed"
    assert input_events[0].status == "interrupted"


@pytest.mark.anyio
async def test_pre_tool_hook_denial_is_reported_without_execution(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, _definition, payload):
            self.calls.append(payload)
            return HookCommandOutput(data={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "blocked by test hook",
                    "additionalContext": "Use the safe tool instead.",
                },
            })

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "check"}],
                "matcher": "test_tool",
            }],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )
    posted = []

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _host_state = await _run_stream(
        monkeypatch,
        [
            _durable_tool_call({
                "type": "tool.call",
                "cid": "cid_test",
                "sid": "sid_test",
                "call_id": "call_test",
                "name": "test_tool",
                "arguments": {"value": 1},
            }),
            {"type": "turn.completed"},
        ],
        hooks=HookRuntime(definitions, command_runner=runner),
    )

    assert result.status == "completed"
    assert len(runner.calls) == 1
    assert posted[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call_test",
        "test_tool",
        False,
    )
    assert posted[0][0][5]["data"]["hook_denied"] is True
    assert posted[0][1]["additional_context"] == (
        "Use the safe tool instead.",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "tool_name",
        "original_arguments",
        "updated_input",
        "expected_arguments",
        "expected_hook_input",
    ),
    [
        (
            "test_tool",
            {"value": 1},
            {"value": 2},
            {"value": 2},
            {"value": 1},
        ),
        (
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** End Patch",
                "force": True,
                "expected_sha256": "sha256:original",
            },
            {"command": "*** Begin Patch\n*** Add File: safe\n+ok\n*** End Patch"},
            {
                "patch": "*** Begin Patch\n*** Add File: safe\n+ok\n*** End Patch",
                "force": True,
                "expected_sha256": "sha256:original",
            },
            {"command": "*** Begin Patch\n*** End Patch"},
        ),
    ],
)
async def test_pre_tool_updated_input_flows_through_approval_and_execution(
    monkeypatch,
    tool_name,
    original_arguments,
    updated_input,
    expected_arguments,
    expected_hook_input,
) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, _definition, payload):
            self.calls.append(payload)
            return HookCommandOutput(data={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated_input,
                },
            })

    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "rewrite"}],
                "matcher": tool_name,
            }],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )
    runner = CommandRunner()
    executed = []
    posted = []

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        executed.append(dict(invocation.arguments))
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

    async def post_tool_approval(*_args, **_kwargs):
        return None

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    approval_event = {
        "type": "tool.approval_required",
        "call_id": "call-rewrite",
        "approval_id": "approval-rewrite",
        "kind": "command",
        "command": "pytest -q",
        "cwd": ".",
        "reason": "模型需要运行测试。",
    }
    call_event = {
        "type": "tool.call",
            "call_id": "call-rewrite",
            "name": tool_name,
            "arguments": original_arguments,
            "reason": "模型需要调用客户端工具。",
    }
    call_event = _durable_tool_call(call_event)

    result, host = await _run_stream(
        monkeypatch,
        [
            approval_event,
            call_event,
            {"type": "turn.completed"},
        ],
        hooks=HookRuntime(definitions, command_runner=runner),
    )

    assert result.status == "completed"
    request = host.frontend.interaction.present_approval.await_args.args[0]
    presentation = request.presentation
    assert presentation.commands == (("pytest -q",),)
    assert presentation.context.kind == "command"
    assert executed == [expected_arguments]
    assert runner.calls[0]["tool_input"] == expected_hook_input
    assert posted[0][0][5] == {
        "ok": True,
        "tool": tool_name,
        "source": "client",
        "args": expected_arguments,
        "text": "done",
        "attachments": [],
        "data": {},
    }
