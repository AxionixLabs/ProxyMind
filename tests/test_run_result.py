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

from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.approval.coordinator import ApprovalCoordinator
from mind_app.approval.models import ApprovalOutcome
from mind_app.interaction.noninteractive import NonInteractiveInteraction
from mind_app.runtime.turns import stream
from agent.application import RunResult
from mind_app.presentation.output.content import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ResponseIdentity,
    SourcesOutput,
)
from mind_app.presentation.output.session import OutputSession
from mind_app.presentation.models import (
    ApprovalView,
    FailureView,
    HookRunView,
    RunIncompleteView,
)
from mind_app.runtime.mcp import tool_runtime
from mind_app.runtime.execution import (
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from mind_app.runtime.turns.executor import (
    TurnExecution,
    build_turn_input_payload,
)
from mind_app.native_coding.exec.exec_policy import ExecPolicyManager
from agent.domain.execution_policy import (
    Decision,
    Policy,
    PrefixPattern,
    PrefixRule,
)
from mind_app.runtime.tools.client_call import (
    ClientToolCallOutcome,
    ClientToolCallResult,
)
from agent.application import ModelCapabilityError, ModelStreamRequest
from agent.adapters.item_reducer import CanonicalItemReducer
from agent.composition import open_effect_journal
from mind_app.runtime.tools.plan_steps import PlanExecutionReport
from infrastructure.hooks.discovery import resolve_hook_definitions
from agent.application import (
    PermissionSettings,
    preset_permissions,
)
from protocol.schema.stream_events import (
    TurnInputAcceptedEvent,
    parse_stream_event as _parse_stream_event,
)
from protocol.client.tools import ToolResultRequestError
from protocol.schema.turn_inputs import TurnInput


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
    async def open(self) -> None:
        return None

    async def stop(self, *, blink: bool = True) -> None:
        _ = blink

    async def record_hidden_output(self, text: str) -> None:
        _ = text

    def record_tool_arguments(self, *_args, **_kwargs) -> None:
        return None


class _OutputStatus(object):
    def __init__(self) -> None:
        self.end_calls: list[bool] = []

    async def begin_tool_status(self) -> None:
        return None

    async def begin_custom_tool_status(self, text: str | None) -> None:
        _ = text

    async def begin_reply_wait_status(
        self,
        text: str | None = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        _ = (text, delay_sec, animate_after_sec)

    async def end_status(self, *, immediate: bool = False) -> None:
        self.end_calls.append(immediate)


class _Sink(object):
    def __init__(self) -> None:
        self.items: list[object] = []

    async def emit(self, item: object) -> None:
        self.items.append(item)


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


def _output_session(*, show_hook_lifecycle: bool = False) -> OutputSession:
    return OutputSession(
        control=_OutputControl(),
        status=_OutputStatus(),
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
    return (
        {"type": "tool.calls.start", **identity, "ready": True},
        payload,
        {"type": "tool.calls.done", **identity},
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


def _mind(
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
    return SimpleNamespace(
        report=SimpleNamespace(output_record_path=""),
        transcripts=transcripts,
        frontend=SimpleNamespace(
            runtime=SimpleNamespace(
                active=frontend_active,
                set_wait_retry_state=Mock(),
            ),
            interaction=interaction,
        ),
        approval_coordinator=ApprovalCoordinator(interaction),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(),
            execution_policy=execution_policy,
        ),
        stop_anim=AsyncMock(),
        freeze_anim=AsyncMock(),
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
    mind_state: SimpleNamespace | None = None,
    show_hook_lifecycle: bool = False,
    permissions: PermissionSettings | None = None,
    effect_journal=None,
    environment_snapshot: dict[str, typing.Any] | None = None,
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
    )
    mind = mind_state or _mind(
        frontend_active=frontend_active,
        effect_journal=effect_journal,
    )

    class ModelCapabilityStub:
        """把测试流工厂适配到正式模型能力端口。"""

        def __init__(self) -> None:
            self.last_stream = None
            self.last_request: ModelStreamRequest | None = None

        class _EventStream(object):
            """为测试迭代器补齐模型流关闭端口。"""

            def __init__(self, iterator, request: ModelStreamRequest) -> None:
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

            async def aclose(self) -> None:
                self.closed = True
                close = getattr(self._iterator, "aclose", None)
                if close is not None:
                    await close()

        def stream(
            self,
            request: ModelStreamRequest,
            *,
            on_reconnect_status=None,
            on_approval_snapshot=None,
        ):
            """按旧测试工厂签名展开冻结请求。"""
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
                on_reconnect_status=on_reconnect_status,
                on_approval_snapshot=on_approval_snapshot,
                **request_options,
            )
            self.last_stream = self._EventStream(iterator, request)
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
                turn_status="running",
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
                terminal=False,
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

    mind.runtime_services.model_capability = ModelCapabilityStub()
    output_session = _output_session(
        show_hook_lifecycle=show_hook_lifecycle
    )
    mind.output_session = output_session
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
    stream_options = {
        "exec_env": environment_snapshot,
        "skills": (
            list(request_skills)
            if request_skills is not None
            else [{"name": "test"}]
        ),
        "turn_execution": turn_execution,
        "session_factory": lambda *_args, **_kwargs: output_session,
    }
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

    result = await stream.stream_turn(
        mind,
        SimpleNamespace(),
        {},
        [],
        **stream_options,
    )
    return result, mind


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

    _result, mind = await _run_stream(
        monkeypatch,
        [
            {"type": "turn.done", "status": "interrupted", "usage": {}},
            {"type": "turn.logical_settled", "next_input": None},
        ],
        environment_snapshot=snapshot,
    )

    request = mind.runtime_services.model_capability.last_request
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

    result, _mind_state = await _run_stream(
        monkeypatch,
        [
            {
                "type": "turn.done",
                "status": "interrupted",
                "usage": {},
            },
            {
                "type": "turn.logical_settled",
                "next_input": None,
            },
        ],
        on_turn_interrupted=lambda: notifications.append("acknowledged"),
    )

    assert result.status == "interrupted"
    assert notifications == ["acknowledged"]


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
    mind = SimpleNamespace(
        external_mcp=SimpleNamespace(current=None),
        client_tools=object(),
        is_service_mcp_linked=lambda: False,
    )

    async def build_context(_service, _external, *, client_registry):
        _ = client_registry
        return SimpleNamespace(session=object(), tools=[])

    async def user_flow(_session, _tools) -> RunResult:
        return expected

    monkeypatch.setattr(tool_runtime, "build_tool_context", build_context)

    runtime = tool_runtime.CompositeToolRuntime(mind)
    result = await runtime.with_session({}, user_flow)

    assert result is expected


@pytest.mark.anyio
async def test_stream_returns_completed_result(monkeypatch) -> None:
    result, mind = await _run_stream(monkeypatch, [
        {"type": "text.delta", "text": "answer"},
        {"type": "text.done"},
        {"type": "turn.done", "usage": {"output_tokens": 3}},
    ])

    assert result == RunResult(
        status="completed",
        assistant_text="answer",
        usage={"output_tokens": 3},
    )
    assert mind.remembered == ["answer"]
    assert mind.runtime_services.model_capability.last_stream.closed is True
    assert mind.output_session.content.items == [
        AssistantTextDelta("answer", response_identity()),
        AssistantSegmentCompleted(response_identity()),
        SourcesOutput(()),
    ]
    assert [
        entry["event"] for entry in mind.transcripts.entries
    ] == [
        "turn.started",
        "message.created",
        "message.created",
        "turn.completed",
    ]
    assert mind.transcripts.entries[1]["actor"] == "user"
    assert mind.transcripts.entries[2] == {
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
    assert not hasattr(mind, "hook_scope")


@pytest.mark.anyio
async def test_stream_projects_deduplicated_canonical_sources(monkeypatch) -> None:
    _result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done"},
    ])

    assert mind.output_session.content.items[-1] == SourcesOutput((
        {"url": "https://example.com/tool"},
        {"url": "https://example.com/text"},
    ))


@pytest.mark.anyio
async def test_stream_persists_named_model_capability_failure(monkeypatch) -> None:
    async def failed_stream(*_args, **_kwargs):
        if False:
            yield None
        raise ModelCapabilityError(
            "model_transport_timeout",
            "model transport timed out",
            retryable=True,
            details={"exception_type": "TimeoutError"},
        )

    result, _mind = await _run_stream(
        monkeypatch,
        [],
        stream_factory=failed_stream,
    )

    assert result == RunResult(
        status="failed",
        error="model transport timed out",
        error_code="model_transport_timeout",
        error_details={"exception_type": "TimeoutError"},
    )


@pytest.mark.anyio
async def test_provider_retry_replaces_partial_answer_in_same_turn(monkeypatch) -> None:
    """验证 provider 断流后只保留新 attempt 正文并切换重试状态。"""
    result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done", "turn_id": "turn_test"},
    ])

    assert result.status == "completed"
    assert result.assistant_text == "new answer"
    assert mind.output_session.content.items == [
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
    assert mind.frontend.runtime.set_wait_retry_state.call_args_list == [
        (("provider",), {}),
        (("idle",), {}),
    ]
    assert [
        entry["event"]
        for entry in mind.transcripts.entries
        if entry["actor"] == "assistant"
    ] == ["message.created", "message.superseded", "message.created"]


@pytest.mark.anyio
async def test_provider_retry_ignores_late_old_item_events(monkeypatch) -> None:
    result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done"},
    ])

    assert [
        item
        for item in mind.output_session.content.items
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
    result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done", "round": 2},
    ])

    assert result.assistant_text == "round one\nround two final"
    assistant_entries = [
        entry
        for entry in mind.transcripts.entries
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
    result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done", "turn_id": "turn_test"},
    ])

    assert result.assistant_text == "answer"
    assert not any(
        isinstance(item, AssistantResponseSuperseded)
        for item in mind.output_session.content.items
    )


@pytest.mark.anyio
async def test_provider_and_transport_retry_statuses_do_not_clear_each_other(
    monkeypatch,
) -> None:
    """验证两个重试来源重叠时只产生一次完整状态区间。"""
    async def overlapping_retry_stream(*_args, **kwargs):
        reconnect_status = kwargs["on_reconnect_status"]
        yield parse_stream_event({
            "type": "turn.retrying",
            "turn_id": "turn_test",
            "round": 1,
                "attempt": 2,
                "max_attempts": 3,
                "retry_in_ms": 20,
        })
        reconnect_status(True)
        reconnect_status(False)
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
            "type": "turn.done",
            "turn_id": "turn_test",
        })

    result, mind = await _run_stream(
        monkeypatch,
        [],
        stream_factory=overlapping_retry_stream,
    )

    assert result.status == "completed"
    assert result.assistant_text == "answer"
    assert mind.frontend.runtime.set_wait_retry_state.call_args_list == [
        (("provider",), {}),
        (("transport",), {}),
        (("provider",), {}),
        (("idle",), {}),
    ]


@pytest.mark.anyio
async def test_stream_preserves_reconciliation_required_without_normal_failure(
    monkeypatch,
) -> None:
    result, mind = await _run_stream(monkeypatch, [{
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
    assert mind.remembered == []
    assert mind.transcripts.entries[-1] == {
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
        for item in mind.output_session.presentation.items
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

    result, mind = await _run_stream(monkeypatch, [
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
            "type": "turn.start",
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
            "type": "turn.done",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
            "status": "completed",
        },
        {
            "proto": "mind.chat",
            "type": "turn.logical_settled",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
        },
    ])

    assert result.status == "completed"
    assert result.assistant_text == "recovered answer"
    assert reconciled_effects == ["effect_known"]
    stream.interrupt_turn.assert_not_awaited()
    assert not any(
        isinstance(item, FailureView)
        and item.phase == "turn.reconciliation_required"
        for item in mind.output_session.presentation.items
    )


@pytest.mark.anyio
async def test_done_finishes_animation_before_logical_settlement(monkeypatch) -> None:
    stream_advanced = asyncio.Event()
    mind = _mind()

    async def pending_stream(*_args, **_kwargs):
        yield parse_stream_event({"type": "turn.done"})
        stream_advanced.set()
        await asyncio.Future()

    task = asyncio.create_task(_run_stream(
        monkeypatch,
        [],
        stream_factory=pending_stream,
        mind_state=mind,
    ))
    await stream_advanced.wait()

    mind.stop_anim.assert_awaited_once_with("wait", settle=False)
    mind.freeze_anim.assert_not_awaited()
    assert mind.output_session.status.end_calls == [True]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_stream_drains_logical_settlement_after_interrupted_done(
    monkeypatch,
) -> None:
    input_events = []

    result, mind = await _run_stream(
        monkeypatch,
        [
            {
                "type": "turn.done",
                "turn_id": "turn_test",
                "status": "interrupted",
            },
            {
                "type": "turn.logical_settled",
                "turn_id": "turn_test",
                "next_input": {
                    "client_message_id": "message_1",
                    "text": "continue next",
                    "attachments": [],
                    "extras": {},
                },
            },
        ],
        on_turn_input_event=input_events.append,
    )

    assert result.status == "interrupted"
    assert len(input_events) == 1
    assert input_events[0].next_input.text == "continue next"
    assert mind.transcripts.entries[-1]["event"] == "turn.interrupted"


@pytest.mark.anyio
async def test_stream_reports_transport_end_after_processing_settlement(
    monkeypatch,
) -> None:
    done = parse_stream_event({
        "type": "turn.done",
        "turn_id": "turn_test",
    })
    settled = parse_stream_event({
        "type": "turn.logical_settled",
        "turn_id": "turn_test",
        "next_input": None,
    })
    processed = []
    stream_ends = []

    class SettledStream(object):
        end_reason = "settled"

        async def _events(self):
            yield done
            yield settled

        def __aiter__(self):
            return self._events()

    def open_stream(*_args, **_kwargs):
        return SettledStream()

    result, _mind = await _run_stream(
        monkeypatch,
        [],
        stream_factory=open_stream,
        on_turn_input_event=lambda event: processed.append(event.type),
        on_turn_stream_end=stream_ends.append,
    )

    assert result.status == "completed"
    assert processed == ["turn.logical_settled"]
    assert stream_ends == ["settled"]


@pytest.mark.anyio
async def test_turn_start_opens_the_control_event_boundary(monkeypatch) -> None:
    input_events = []

    result, _mind = await _run_stream(
        monkeypatch,
        [
            {"type": "turn.start", "turn_id": "turn_test"},
            {"type": "turn.done", "turn_id": "turn_test"},
        ],
        on_turn_input_event=input_events.append,
    )

    assert result.status == "completed"
    assert [event.type for event in input_events] == ["turn.start"]


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

    _result, mind = await _run_stream(
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
            {"type": "turn.done", "turn_id": "turn_test"},
        ],
        on_turn_input_event=handle_input,
    )

    messages = [
        (entry["actor"], entry["payload"])
        for entry in mind.transcripts.entries
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
    _result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done", "usage": {}},
    ])

    ordered = [
        (entry["event"], entry["actor"], entry["payload"])
        for entry in mind.transcripts.entries
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
    _result, mind = await _run_stream(
        monkeypatch,
        [{"type": "turn.done", "usage": {}}],
        attachments=({"filename": "screen.png"},),
        extras={"selection": "src/app.py"},
    )

    user_entry = next(
        entry
        for entry in mind.transcripts.entries
        if entry["actor"] == "user"
    )
    assert user_entry["payload"] == {
        "content": "hello",
        "attachments": [{"filename": "screen.png"}],
        "extras": {"selection": "src/app.py"},
    }


@pytest.mark.anyio
async def test_child_stream_does_not_mutate_root_frontend_state(monkeypatch) -> None:
    result, mind = await _run_stream(
        monkeypatch,
        [
            {"type": "text.delta", "text": "child answer"},
            {"type": "turn.done"},
        ],
        child_agent=True,
        frontend_active=False,
    )

    assert result.status == "completed"
    assert result.assistant_text == "child answer"
    assert mind.remembered == []
    mind.stop_anim.assert_not_awaited()


@pytest.mark.anyio
async def test_child_stream_failure_does_not_stop_root_animation(monkeypatch) -> None:
    async def fail_stream(*_args, **_kwargs):
        raise RuntimeError("child stream failed")
        if False:
            yield None

    result, mind = await _run_stream(
        monkeypatch,
        [],
        child_agent=True,
        frontend_active=False,
        stream_factory=fail_stream,
    )

    assert result.status == "failed"
    mind.stop_anim.assert_not_awaited()


@pytest.mark.anyio
async def test_stream_preserves_explicit_empty_skills(monkeypatch) -> None:
    async def stream_with_no_skills(*_args, **kwargs):
        assert kwargs["skills"] == []
        yield parse_stream_event({"type": "turn.done"})

    result, _mind_state = await _run_stream(
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
        yield parse_stream_event({"type": "turn.done"})

    result, _mind_state = await _run_stream(
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
                return SimpleNamespace(data={
                    "systemMessage": "session system",
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "session context",
                    },
                })
            return SimpleNamespace(data={
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
        yield parse_stream_event({"type": "turn.done"})

    result, _mind_state = await _run_stream(
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
            return SimpleNamespace(data={})

    definitions = resolve_hook_definitions(
        {
            "SessionStart": [_hook("start", matcher="startup")],
            "UserPromptSubmit": [_hook("prompt")],
            "Stop": [_hook("stop")],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, mind = await _run_stream(
        monkeypatch,
        [{"type": "turn.done"}],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        session_started=True,
        show_hook_lifecycle=True,
    )

    assert result.status == "completed"
    hook_views = [
        item
        for item in mind.output_session.presentation.items
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
            return SimpleNamespace(data={
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

    result, mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        session_started=True,
    )

    assert result.status == "failed"
    assert result.error == "configure first"
    assert mind_state.queued_context == [("Python 3.13 is required",)]


@pytest.mark.anyio
async def test_prompt_stop_queues_context_for_next_turn(monkeypatch) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
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

    result, mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "failed"
    assert result.error == "Select a project first."
    assert mind_state.queued_context == [(
        "Available projects: web, app, service.",
    )]


@pytest.mark.anyio
async def test_child_prompt_stop_returns_context_without_queuing_root(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
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

    result, mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        child_agent=True,
    )

    assert result.status == "failed"
    assert result.additional_context == (
        "Available projects: web, app, service.",
    )
    assert mind_state.queued_context == []


@pytest.mark.anyio
async def test_stream_runs_turn_hooks_from_one_scope(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            return SimpleNamespace(data={})

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

    result, mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done", "usage": {"output_tokens": 2}}],
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
    assert mind_state.transcripts.entries[0] == {
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
                return SimpleNamespace(data={
                    "continue": False,
                    "stopReason": "root startup policy",
                })
            if definition.event == "Stop":
                return SimpleNamespace(data={
                    "decision": "block",
                    "reason": "root continuation",
                })
            return SimpleNamespace(data={})

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

    result, _mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done"}],
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
            return SimpleNamespace(data={})

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

    result, mind = await _run_stream(
        monkeypatch,
        [{"type": "turn.done"}],
        hooks=resolved_runtime,
        hook_scope_factory=lambda context: HookExecutionScope(
            context=context,
            dispatcher=injected_runtime,
        ),
    )

    assert result.status == "completed"
    assert injected_runner.events == ["UserPromptSubmit", "Stop"]
    assert resolved_runner.events == []
    assert not hasattr(mind, "hook_scope")


@pytest.mark.anyio
async def test_prompt_hook_denial_skips_stop_and_continuation(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            if definition.event == "UserPromptSubmit":
                return SimpleNamespace(data={
                    "continue": False,
                    "stopReason": "prompt blocked",
                })
            return SimpleNamespace(data={
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

    result, _mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done"}],
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
            return SimpleNamespace(data={})

    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, _mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done", "usage": {"output_tokens": 2}}],
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
            return SimpleNamespace(data={
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
                return SimpleNamespace(data={
                    "decision": "block",
                    "reason": "continue once",
                    "systemMessage": "stop system",
                })
            return SimpleNamespace(data={})

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
            "type": "turn.done",
            "turn_id": kwargs["turn_id"],
        })

    result, _mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=runner),
        stream_factory=stream_with_continuation,
    )

    assert result.status == "completed"
    assert result.assistant_text == "reply 2"
    assert messages == ["hello", "continue once"]
    assert "additional_context" not in request_kwargs[1]
    assert "system_message" not in request_kwargs[1]
    assert [payload["stop_hook_active"] for payload in runner.payloads] == [
        False,
        True,
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("can_continue", "expected_status", "expected_messages"),
    (
        (False, "incomplete", ["hello"]),
        (True, "completed", ["hello", "continue once"]),
    ),
)
async def test_incomplete_turn_gates_stop_hook_continuation(
    monkeypatch,
    can_continue: bool,
    expected_status: str,
    expected_messages: list[str],
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, payload):
            if not payload["stop_hook_active"]:
                return SimpleNamespace(data={
                    "decision": "block",
                    "reason": "continue once",
                })
            return SimpleNamespace(data={})

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
                "type": "turn.done",
                "turn_id": kwargs["turn_id"],
                "status": "incomplete",
                "reason": "max_output_tokens",
                "can_continue": can_continue,
                "response_id": "msg_1",
                "route": "messages",
                "usage": {"output_tokens": 7},
                "stop_reason": "max_tokens",
            })
            return
        yield parse_stream_event({
            "type": "turn.done",
            "turn_id": kwargs["turn_id"],
            "status": "completed",
        })

    result, mind = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        stream_factory=terminal_stream,
    )

    assert result.status == expected_status
    assert messages == expected_messages
    if can_continue:
        return
    assert result.reason == "max_output_tokens"
    assert result.can_continue is False
    assert result.stop_reason == "max_tokens"
    assert result.usage == {"output_tokens": 7}
    assert mind.transcripts.entries[-1]["event"] == "turn.incomplete"
    assert mind.transcripts.entries[-1]["payload"]["stop_reason"] == (
        "max_tokens"
    )
    assert mind.transcripts.entries[-1]["payload"]["can_continue"] is False
    assert isinstance(
        mind.output_session.presentation.items[-1],
        RunIncompleteView,
    )


@pytest.mark.anyio
async def test_pause_turn_failure_does_not_run_stop_hook_continuation(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
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
            "type": "turn.failed",
            "status": "failed",
            "error": "pause_turn is not supported",
            "route": "messages",
            "usage": {"output_tokens": 2},
            "stop_reason": "pause_turn",
        })

    result, mind = await _run_stream(
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
    assert mind.transcripts.entries[-1]["payload"]["stop_reason"] == (
        "pause_turn"
    )
    failure_view = mind.output_session.presentation.items[-1]
    assert isinstance(failure_view, FailureView)
    assert failure_view.error == "pause_turn is not supported"
    assert failure_view.stop_reason == "pause_turn"
    assert failure_view.usage == {"output_tokens": 2}


@pytest.mark.anyio
async def test_stream_returns_failed_result(monkeypatch) -> None:
    result, mind = await _run_stream(monkeypatch, [
        {"type": "turn.failed", "error": "request failed"},
    ])

    assert result.status == "failed"
    assert result.error == "request failed"
    assert result.exit_code == 1
    mind.stop_anim.assert_awaited_once_with("wait", settle=False)


@pytest.mark.anyio
async def test_stream_without_terminal_event_is_incomplete(monkeypatch) -> None:
    result, _mind_state = await _run_stream(monkeypatch, [])

    assert result.status == "incomplete"
    assert result.error == "stream ended before turn completion"


@pytest.mark.anyio
async def test_stream_emits_assistant_boundary_before_structured_output(monkeypatch) -> None:
    result, mind = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "segment_id": "first-item",
            "text": "first",
        },
        {"type": "text.done", "segment_id": "first-item"},
        {"type": "tool.builtin.call"},
        {"type": "tool.builtin.done"},
        {
            "type": "text.delta",
            "segment_id": "second-item",
            "text": "second",
        },
        {"type": "text.done", "segment_id": "second-item"},
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    assert mind.output_session.content.items == [
        AssistantTextDelta("first", response_identity()),
        AssistantSegmentCompleted(response_identity()),
        AssistantOutputBoundary(),
        AssistantTextDelta("second", response_identity()),
        AssistantSegmentCompleted(response_identity()),
        SourcesOutput(()),
    ]


@pytest.mark.anyio
async def test_stream_commits_output_before_tool_round_transition(
    monkeypatch,
) -> None:
    result, mind = await _run_stream(monkeypatch, [
        {
            "type": "text.delta",
            "round": 1,
            "segment_id": "round-one-item",
            "text": "first",
        },
        {
            "type": "tool.builtin.call",
            "round": 2,
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
            "type": "turn.done",
            "round": 2,
        },
    ])

    assert result == RunResult(
        status="completed",
        assistant_text="first\nsecond",
    )
    assert mind.output_session.content.items == [
        AssistantTextDelta("first", response_identity(round_no=1)),
        AssistantOutputBoundary(),
        AssistantTextDelta("second", response_identity(round_no=2)),
        AssistantSegmentCompleted(response_identity(round_no=2)),
        SourcesOutput(()),
    ]
    assert [
        entry["payload"]
        for entry in mind.transcripts.entries
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
    _result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done", "round": 2},
    ])

    messages = [
        entry["payload"]["content"]
        for entry in mind.transcripts.entries
        if entry["event"] == "message.created" and entry["actor"] == "assistant"
    ]
    assert messages == ["first", "second"]
    assert mind.output_session.content.items == [
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
    _result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done"},
    ])

    assistant_entries = [
        entry
        for entry in mind.transcripts.entries
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

    result, _mind_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "cid": "cid_test",
            "sid": "sid_test",
            "call_id": "call-client",
            "name": "test_tool",
            "arguments": {"value": 1},
            "reason": "模型需要调用客户端工具。",
        }),
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
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

    result, _mind_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-reconcile",
            "name": "test_tool",
            "arguments": {"value": 1},
        }),
        {"type": "turn.done"},
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

    result, _mind_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-ack",
            "name": "test_tool",
            "arguments": {},
        }),
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    assert statuses == 1
    assert len(request_ids) == 2
    assert request_ids[0] == request_ids[1]


@pytest.mark.anyio
async def test_stream_stops_deterministic_tool_result_terminal_without_failure(
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

    result, mind = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-closed",
            "name": "test_tool",
            "arguments": {},
        }),
        {"type": "turn.done"},
    ])

    assert result.status == "interrupted"
    assert result.error == (
        "tool_call_turn_closed: turn no longer accepts tool results"
    )
    status_query.assert_not_awaited()
    failure_views = [
        item
        for item in mind.output_session.presentation.items
        if isinstance(item, FailureView)
    ]
    assert failure_views[-1].phase == "turn.tool_result_delivery_stopped"


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

    result, _mind_state = await _run_stream(
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
            {"type": "turn.done"},
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
            return SimpleNamespace(data={
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

    result, mind_state = await _run_stream(
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
    assert mind_state.queued_context == [("inspect protected paths",)]


@pytest.mark.anyio
async def test_post_tool_hook_replaces_plan_result_for_model(
    monkeypatch,
    tmp_path,
) -> None:
    effect_journal = _open_effect_journal(tmp_path / "effects.db")
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
                "replacementResult": {
                    "ok": False,
                    "text": "plan result replaced",
                    "data": {"replaced": True},
                },
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

    result, _mind_state = await _run_stream(
        monkeypatch,
        [
            _durable_tool_call({
                "type": "tool.call",
                "call_id": "call-plan",
                "name": PLAN_STEPS_TOOL,
                "arguments": {"steps": []},
            }),
            {"type": "turn.done"},
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
        False,
    )
    assert posted[0][0][5]["data"] == {"replaced": True}
    assert posted[0][1]["additional_context"] == ("explain replacement",)
    assert "system_message" not in posted[0][1]


@pytest.mark.anyio
async def test_child_approval_uses_local_agent_identity(monkeypatch) -> None:
    approval_posts = []

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, mind = await _run_stream(
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
            {"type": "turn.done"},
        ],
        child_agent=True,
    )

    assert result.status == "completed"
    request = mind.frontend.interaction.present_approval.await_args.args[0]
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
            return SimpleNamespace(data={
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

    result, mind = await _run_stream(
        monkeypatch,
        [{
            "type": "tool.approval_required",
            "call_id": "call-denied",
            "approval_id": "approval-denied",
            "kind": "command",
            "command": "pytest -q",
            "cwd": ".",
            "reason": "模型需要运行测试。",
        }, {"type": "turn.done"}],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "completed"
    mind.frontend.interaction.present_approval.assert_awaited_once()
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

    result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    mind.frontend.interaction.present_approval.assert_awaited_once()
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

    result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    assert len(executions) == 1
    assert len(result_posts) == 1
    mind.frontend.interaction.present_approval.assert_awaited_once()


@pytest.mark.anyio
async def test_confirmed_approval_cannot_override_local_forbidden_rule(
    monkeypatch,
) -> None:
    executions = []
    result_posts = []
    mind = _mind()
    mind.workspace_runtime.execution_policy.policy = Policy.from_parts([
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

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    monkeypatch.setattr(stream, "post_tool_approval", AsyncMock())

    result, _ = await _run_stream(monkeypatch, [
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
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-forbidden-after-remote-approval",
            "name": "shell_command",
            "arguments": {"command": "rm -rf build"},
            "reason": "模型需要清理构建目录。",
        }),
        {"type": "turn.done"},
    ], mind_state=mind)

    assert result.status == "completed"
    assert executions == []
    assert len(result_posts) == 1
    assert result_posts[0][0][4] is False


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

    result, _mind_state = await _run_stream(monkeypatch, [
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

    result, _mind_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-amendment",
            "name": "shell_command",
            "arguments": {"command": "rm -rf build"},
            "reason": "清理临时构建目录。",
        }),
        {"type": "turn.done"},
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

    result, _mind_state = await _run_stream(monkeypatch, [
        _durable_tool_call({
            "type": "tool.call",
            "call_id": "call-without-reason",
            "name": "shell_command",
            "arguments": {"command": "rm -rf build"},
        }),
        {"type": "turn.done"},
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

    mind = _mind()
    mind.workspace_runtime.coding = SimpleNamespace(
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
        [patch_event, {"type": "turn.done"}],
        mind_state=mind,
        permissions=untrusted,
    )
    second, _ = await _run_stream(
        monkeypatch,
        [patch_event, {"type": "turn.done"}],
        mind_state=mind,
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

    result, mind = await _run_stream(monkeypatch, [
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
        {"type": "turn.done", "status": "completed"},
    ])

    assert result.status == "completed"
    assert result.assistant_text == "我会换一种方式。"
    assert approval_posts[0][1]["decision"] == "decline"
    tool_entry = next(
        entry
        for entry in mind.transcripts.entries
        if entry["event"] == "tool.completed"
    )
    assert tool_entry["payload"]["status"] == "declined"
    approval_views = [
        item
        for item in mind.output_session.presentation.items
        if isinstance(item, ApprovalView)
    ]
    assert [view.decision for view in approval_views] == ["decline"]
    assert len(mind.output_session.presentation.items) == 3


@pytest.mark.anyio
async def test_noninteractive_approval_decline_is_attributed_to_policy(
    monkeypatch,
) -> None:
    monkeypatch.setattr(stream, "post_tool_approval", AsyncMock())
    mind_state = _mind(frontend_active=False)
    mind_state.approval_coordinator = ApprovalCoordinator(
        NonInteractiveInteraction()
    )

    result, mind = await _run_stream(
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
            {"type": "turn.done", "status": "completed"},
        ],
        frontend_active=False,
        mind_state=mind_state,
    )

    assert result.status == "completed"
    approval_view = next(
        item
        for item in mind.output_session.presentation.items
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

    result, mind = await _run_stream(
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
                "type": "turn.done",
                "turn_id": "turn_test",
                "status": "interrupted",
            },
            {
                "type": "turn.logical_settled",
                "turn_id": "turn_test",
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
        for entry in mind.transcripts.entries
        if entry["event"] == "tool.completed"
    )
    assert tool_entry["payload"]["status"] == "cancelled"
    approval_views = [
        item
        for item in mind.output_session.presentation.items
        if isinstance(item, ApprovalView)
    ]
    assert [view.decision for view in approval_views] == ["cancel"]
    assert len(mind.output_session.presentation.items) == 2
    assert len(input_events) == 1
    assert input_events[0].type == "turn.logical_settled"


@pytest.mark.anyio
async def test_pre_tool_hook_denial_is_reported_without_execution(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, _definition, payload):
            self.calls.append(payload)
            return SimpleNamespace(data={
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

    result, _mind_state = await _run_stream(
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
            {"type": "turn.done"},
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
            return SimpleNamespace(data={
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

    result, mind = await _run_stream(
        monkeypatch,
        [
            approval_event,
            call_event,
            {"type": "turn.done"},
        ],
        hooks=HookRuntime(definitions, command_runner=runner),
    )

    assert result.status == "completed"
    request = mind.frontend.interaction.present_approval.await_args.args[0]
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
