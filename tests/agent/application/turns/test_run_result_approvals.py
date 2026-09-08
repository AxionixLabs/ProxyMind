# -*- coding: utf-8 -*-

"""验证审批、review、本地策略与工具执行的因果顺序。

这些断言共享同一审批状态序列，继续拆分会复制驱动器并削弱顺序约束。
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
