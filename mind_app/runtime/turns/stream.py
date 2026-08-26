# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from mind_app.mcp.contracts import McpSessionLike
from mind_app.mcp.tool_store import meta_for_tool
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_core.skills import skills_payload
from mind_nova.tool_approval import TOOL_APPROVAL_ACCEPT_DECISIONS
from mind_app.approval.policy import (
    approval_decisions,
    approval_execpolicy_amendment,
    approval_from_event,
    approval_id_from_event,
    approval_reason,
)
from mind_app.approval.ledger import ApprovalCallLedger
from mind_app.approval.models import ApprovalDecisionValue
from mind_nova.events import EventReport
from mind_nova.requests.chat import stream_chat
from mind_nova.identifiers import stable_request_id
from mind_nova.requests.turn_control import (
    TurnControlRequestError,
    interrupt_turn
)
from mind_nova.turn_inputs import TurnInput
from mind_app.frontend.contracts import WaitRetryState
from mind_nova.stream_events import (
    TextDeltaEvent,
    TextDoneEvent,
    TextMetaEvent,
    PresentationSupersededEvent,
    StreamEvent,
    ToolApprovalRequiredEvent,
    ToolBuiltinDoneEvent,
    ToolCallEvent,
    ToolEvent,
    ToolOutputEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
    TurnReconciliationRequiredEvent,
    TurnRetryingEvent,
    TurnTerminalEvent
)
from mind_nova.requests.tools import (
    post_tool_approval,
    post_tool_result
)
from ...output import (
    AssistantOutputBoundary,
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    OutputControlPort,
    ResponseIdentity,
    SessionFactory,
    SourcesOutput
)
from ...output.session import OutputSession
from ..hooks.presentation import HookPresentationAdapter
from .result import (
    RunResult,
    RunStatus
)
from ...presentation.approval_views import build_approval_view
from ...presentation.models import ApprovalSource
from ...presentation.run_views import (
    build_run_completed_view,
    build_run_incomplete_view,
    build_run_started_view
)
from ..support.loop_support import finish_failure
from ..execution import (
    ToolInvocation,
    TurnContext
)
from ..hooks.tool import ToolCallCoordinator
from ..hooks.models import StopHookDecision
from ..hooks.turn import (
    PromptHookBlockedError,
    TurnHookEvents
)
from ..environment.exec_env import build_runtime_exec_env
from ..support.session_policy import friendly_exception_text
from ..tools.run import server_tool_output_result
from ..tools.display import show_tool_result
from ...native_coding.exec.exec_policy import (
    ExecApprovalRequirement,
    ExecPolicyManager,
    validate_sandbox_permission_arguments,
)
from ..tools.client_call import ClientToolCallRunner
from ..durable_effects import LocalEffectReconciliationRequired
from ..tools.plan_call import PlanToolCallRunner
from .executor import (
    TurnExecution,
    build_turn_input_payload,
    create_continuation_execution,
    record_turn_finished,
    record_turn_started,
    turn_continuation_count
)
from ..support.idle_status import IdleStatusTimer
from ...stream_events.tool_trace import coding_trace_tool
from ...stream_events.lifecycle import handle_lifecycle_event
from ...stream_events.assistant_boundary import is_assistant_output_boundary
from ...stream_state.segment import SegmentTracker
from engine.observability import (
    observe,
    observe_exception
)

if typing.TYPE_CHECKING:
    from ...controller import Mind

MAX_STOP_CONTINUATIONS = 3
LOCAL_EXEC_POLICY_TOOLS = frozenset({
    "shell_command",
    "exec_command",
    "write_stdin",
})


def _local_exec_policy_requirement(
    manager: ExecPolicyManager,
    turn_context: TurnContext,
    *,
    tool: str,
    arguments: dict[str, typing.Any],
    call_id: str,
) -> ExecApprovalRequirement | None:
    """返回 shell/exec 工具的本地执行要求。"""
    if tool not in LOCAL_EXEC_POLICY_TOOLS:
        return None
    command = arguments.get("command")
    if tool == "write_stdin":
        command = arguments.get("stdin")
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        sandbox_permissions = validate_sandbox_permission_arguments(arguments)
    except ValueError as error:
        return ExecApprovalRequirement.forbidden(str(error))
    try:
        return manager.create_exec_approval_requirement_for_command(
            command,
            approval_policy=turn_context.permissions.approval_policy,
            sandbox_mode=turn_context.permissions.sandbox_mode,
            cwd=arguments.get("cwd") or turn_context.cwd,
            tool=tool,
            amendment_id=f"local-rule-{call_id}",
            sandbox_permissions=sandbox_permissions,
            environment_id=arguments.get("environment_id"),
            tty=arguments.get("tty"),
            additional_permissions=arguments.get("additional_permissions"),
            policy_fingerprint=arguments.get("policy_fingerprint"),
            patch_scope=arguments.get("patch_scope"),
        )
    except ValueError as error:
        return ExecApprovalRequirement.forbidden(str(error))


def _local_exec_policy_approval(
    *,
    invocation: ToolInvocation,
    requirement: ExecApprovalRequirement,
) -> dict[str, typing.Any]:
    """构造客户端本地执行策略审批请求。"""
    command = str(
        invocation.arguments.get("command")
        or invocation.arguments.get("stdin")
        or ""
    )
    approval: dict[str, typing.Any] = {
        "id": f"local-exec-{invocation.call_id}",
        "request_id": f"local-exec-{invocation.call_id}",
        "call_id": invocation.call_id,
        "tool": invocation.name,
        "arguments": dict(invocation.arguments),
        "command": command,
        "cwd": str(invocation.arguments.get("cwd") or invocation.turn.cwd),
        "risk": "dangerous_command" if requirement.state == "needs_approval" else "local_policy",
        "category": "exec",
        "reasons": ["local_exec_policy"],
        "environment": (
            "host"
            if str(invocation.arguments.get("sandbox_permissions") or "")
            .strip()
            .casefold() == "require_escalated"
            else "local"
        ),
    }
    for field_name in (
        "environment_id",
        "tty",
        "additional_permissions",
        "policy_fingerprint",
        "patch_scope",
    ):
        value = invocation.arguments.get(field_name)
        if value not in (None, "", (), [], {}):
            approval[field_name] = value
    justification = str(
        invocation.arguments.get("justification") or invocation.reason or ""
    ).strip()
    if (
        not justification
        and str(invocation.arguments.get("sandbox_permissions") or "")
        .strip()
        .casefold()
        == "require_escalated"
    ):
        justification = "Command requested host shell execution."
    if justification:
        approval["justification"] = justification
    if requirement.reason:
        approval["reason"] = requirement.reason
    if invocation.reason:
        approval["approval_reason"] = invocation.reason
    selected_reason = approval_reason(approval)
    if selected_reason:
        approval["reason"] = selected_reason
        approval["justification"] = selected_reason
    amendment = requirement.proposed_execpolicy_amendment
    if amendment is not None:
        approval["proposed_execpolicy_amendment"] = {
            "id": amendment.id,
            "command_prefix": list(amendment.command_prefix),
            "display": amendment.display,
        }
    return approval


def _apply_local_exec_policy_approval(
    manager: ExecPolicyManager,
    *,
    invocation: ToolInvocation,
    approval: dict[str, typing.Any],
    decision: ApprovalDecisionValue,
) -> str | None:
    """把本地审批的会话或持久化选择写入策略管理器。"""
    command = str(
        invocation.arguments.get("command")
        or invocation.arguments.get("stdin")
        or ""
    )
    cwd = invocation.arguments.get("cwd") or invocation.turn.cwd
    try:
        if decision == "acceptForSession":
            manager.add_approval_for_session(
                command,
                tool=invocation.name,
                cwd=cwd,
                sandbox_permissions=invocation.arguments.get("sandbox_permissions"),
                environment_id=invocation.arguments.get("environment_id"),
                tty=invocation.arguments.get("tty"),
                additional_permissions=invocation.arguments.get(
                    "additional_permissions"
                ),
                policy_fingerprint=invocation.arguments.get("policy_fingerprint"),
                patch_scope=invocation.arguments.get("patch_scope"),
            )
        elif decision == "acceptWithExecpolicyAmendment":
            amendment = approval_execpolicy_amendment(approval)
            if amendment is None:
                return "local execution policy amendment is invalid"
            manager.persist_execpolicy_amendment({
                "command_prefix": list(amendment.command_prefix),
            })
    except (OSError, UnicodeError, ValueError) as error:
        return str(error).strip() or type(error).__name__
    return None


def _local_exec_policy_denied_result(
    requirement: ExecApprovalRequirement,
) -> dict[str, typing.Any]:
    """构造本地执行策略拒绝结果。"""
    return {
        "execution_denied": True,
        "error": "local execution policy forbids command",
        "decision": requirement.state,
        "reason": requirement.reason,
    }


def _local_exec_policy_cancelled_result() -> dict[str, typing.Any]:
    """构造本地审批取消后的未执行结果。"""
    return {
        "ok": False,
        "text": "user cancelled",
        "data": {
            "executed": False,
            "status": "cancelled",
        },
    }


class _RetryingStatus(object):
    """合并传输重连与 provider 重试状态后通知展示层。"""

    def __init__(
        self,
        sink: typing.Callable[[WaitRetryState], None] | None,
    ) -> None:
        """绑定状态回调并初始化两个独立重试原因。"""
        self.sink      = sink
        self.transport = False
        self.provider  = False

        self.state: WaitRetryState = "idle"

    def set_transport(self, retrying: bool) -> None:
        """更新事件传输重连状态。"""
        self.transport = bool(retrying)
        self._refresh()

    def set_provider(self, retrying: bool) -> None:
        """更新供应商流重试状态。"""
        self.provider = bool(retrying)
        self._refresh()

    def close(self) -> None:
        """清除所有重试原因并结束可见状态。"""
        self.transport = False
        self.provider = False
        self._refresh()

    def _refresh(self) -> None:
        """按传输优先级合并重试来源并通知展示层。"""
        state: WaitRetryState = (
            "transport"
            if self.transport
            else "provider"
            if self.provider
            else "idle"
        )
        if state == self.state:
            return
        self.state = state
        if self.sink is not None:
            self.sink(state)


def _optional_callback(
    value: typing.Any,
    *,
    name: str
) -> typing.Callable[..., typing.Any] | None:
    """校验可选回调并返回可调用边界。"""
    if value is None:
        return None
    if not callable(value):
        raise TypeError(f"{name} must be callable")
    return value


def _resolve_output_session_factory(
    value: typing.Any,
) -> SessionFactory:
    """解析单轮输出工厂并校验续跑边界传入值。"""
    if value is None:
        raise RuntimeError("stream output session factory is required")
    if not callable(value):
        raise TypeError("session_factory must be callable")
    return value


def _response_identity(
    event: StreamEvent,
    tracker: SegmentTracker,
) -> ResponseIdentity:
    """把当前领域事件映射为机器输出使用的稳定响应身份。"""
    presentation_epoch, round_no, attempt = tracker.response_identity(event)
    return ResponseIdentity(
        turn_id=event.turn_id,
        presentation_epoch=presentation_epoch,
        round=round_no,
        attempt=attempt,
    )


def _terminal_result_fields(event: TurnTerminalEvent) -> dict[str, typing.Any]:
    """提取需要保留到运行结果和会话记录的终态字段。"""
    fields: dict[str, typing.Any] = {}
    for field_name in (
        "response_id",
        "model",
        "route",
        "request_id",
        "service_tier",
        "stop_reason",
        "stop_sequence",
    ):
        value = getattr(event, field_name)
        if value not in {None, ""}:
            fields[field_name] = value
    if isinstance(event, TurnDoneEvent):
        if event.reason:
            fields["reason"] = event.reason
        if event.can_continue is not None:
            fields["can_continue"] = event.can_continue
    return fields


def _tool_invocation_from_event(
    turn_context: TurnContext,
    event: ToolEvent,
    tools: list[dict[str, typing.Any]],
    *,
    arguments: dict[str, typing.Any] | None = None,
    name: str | None = None,
) -> ToolInvocation:
    """从流式事件构建不暴露内部授权字段的工具调用上下文。"""
    event_name = str(name or getattr(event, "name", "")).strip()
    local_meta = meta_for_tool(tools, event_name)
    event_arguments = getattr(event, "arguments", {})

    return ToolInvocation(
        turn=turn_context,
        call_id=event.call_id,
        name=event_name,
        arguments=dict(event_arguments if arguments is None else arguments),
        meta=local_meta,
        reason=event.reason,
    )


def _approval_with_updated_input(
    approval: dict[str, typing.Any],
    tool: str,
    effective_arguments: dict[str, typing.Any] | None,
) -> dict[str, typing.Any]:
    """返回应用 Hook 参数改写后的审批数据。"""
    if effective_arguments is None:
        return approval

    updated = dict(approval)
    updated["tool"] = tool or updated.get("tool") or ""
    updated["arguments"] = dict(effective_arguments)

    if tool in {"shell_command", "exec_command", "apply_patch"}:
        command_field = "patch" if tool == "apply_patch" else "command"
        command = effective_arguments.get(command_field)
        if isinstance(command, str):
            updated["command"] = command
    return updated


def _approval_report_kwargs(
    approval: dict[str, typing.Any],
    *,
    decision: ApprovalDecisionValue,
    source: ApprovalSource,
    turn_id: str,
    hook_reason: str = "",
    additional_context: typing.Sequence[str] = (),
) -> dict[str, typing.Any]:
    """构造审批决定回传所需的协议字段。"""
    approved = decision in TOOL_APPROVAL_ACCEPT_DECISIONS
    if approved:
        reason = None
    elif source == "hook":
        reason = hook_reason
    elif source == "policy":
        reason = "approval policy is never"
    elif decision == "cancel":
        reason = "user cancelled"
    else:
        reason = "user denied"

    fields: dict[str, typing.Any] = {
        "decision": decision,
        "reason": reason,
        "turn_id": turn_id,
    }
    if decision == "acceptWithExecpolicyAmendment":
        amendment = approval_execpolicy_amendment(approval)
        if amendment is None:
            raise RuntimeError("approval amendment decision is missing proposal")
        fields["execpolicy_amendment_id"] = amendment.id
    if not approved and additional_context:
        fields["additional_context"] = additional_context
    return fields


def _hook_denied_result(reason: str) -> dict[str, typing.Any]:
    """构建前置 Hook 阻止工具时的标准结果。"""
    text = str(reason or "tool use denied by hook")
    return {
        "ok": False,
        "text": text,
        "data": {
            "hook_denied": True,
            "error": text,
        },
    }


def _extend_request_context(
    kwargs: dict[str, typing.Any],
    *,
    additional_context: typing.Iterable[str] = (),
    system_message: str = ""
) -> None:
    """把 Hook 注入文本合并到请求参数。"""
    contexts = [
        text
        for value in additional_context
        for text in [str(value or "").strip()]
        if text
    ]
    if contexts:
        existing = kwargs.get("additional_context")
        merged = list(existing) if isinstance(existing, list) else []
        merged.extend(contexts)
        kwargs["additional_context"] = merged

    system_text = str(system_message or "").strip()
    if system_text:
        kwargs["system_message"] = _join_text(
            str(kwargs.get("system_message") or ""),
            system_text,
        )


def _join_text(*values: str) -> str:
    """合并非空文本段。"""
    return "\n\n".join(
        text
        for value in values
        for text in [str(value or "").strip()]
        if text
    )


async def _discard_stop_hook_decision(
    awaitable: typing.Awaitable[StopHookDecision]
) -> None:
    """执行停止 Hook 并丢弃清理阶段不应消费的续跑决定。"""
    await awaitable


async def _cancel_reconciliation_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    effect_id: str
) -> bool:
    """使用稳定中断命令释放无法自动核对的持久轮次。"""
    request_id = stable_request_id(
        "reconciliation_cancel",
        cid,
        sid,
        turn_id,
        effect_id,
    )
    for attempt in range(2):
        try:
            response = await interrupt_turn(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                request_id=request_id,
            )
            return response.status in {"accepted", "turn_not_active"}
        except TurnControlRequestError:
            if attempt == 0:
                continue
            return False
    return False


async def _interrupt_approval_cancelled_turn(
    *,
    cid: str,
    sid: str,
    turn_id: str,
    call_id: str,
) -> bool:
    """中断本地审批取消对应的逻辑轮次。"""
    request_id = stable_request_id(
        "approval_cancel",
        cid,
        sid,
        turn_id,
        call_id,
    )
    for attempt in range(2):
        try:
            response = await interrupt_turn(
                cid=cid,
                sid=sid,
                turn_id=turn_id,
                request_id=request_id,
            )
            return response.status in {"accepted", "turn_not_active"}
        except TurnControlRequestError:
            if attempt == 0:
                continue
            return False
    return False


async def stream_turn(
    mind: "Mind",
    session: McpSessionLike,
    pref_config: dict[str, typing.Any],
    tools: list[dict[str, typing.Any]],
    *_,
    turn_execution: TurnExecution,
    **kwargs
) -> RunResult:
    """处理流式事件、工具调用和输出上报。"""
    on_turn_input_context = _optional_callback(
        kwargs.pop("on_turn_input_context", None),
        name="on_turn_input_context",
    )
    on_turn_input_event = _optional_callback(
        kwargs.pop("on_turn_input_event", None),
        name="on_turn_input_event",
    )
    on_turn_stream_end = _optional_callback(
        kwargs.pop("on_turn_stream_end", None),
        name="on_turn_stream_end",
    )
    on_turn_interrupted = _optional_callback(
        kwargs.pop("on_turn_interrupted", None),
        name="on_turn_interrupted",
    )
    on_retry_state = _optional_callback(
        kwargs.pop("on_retry_state", None),
        name="on_retry_state",
    )

    reentry_kwargs = dict(kwargs)
    if on_turn_input_context is not None:
        reentry_kwargs["on_turn_input_context"] = on_turn_input_context
    if on_turn_input_event is not None:
        reentry_kwargs["on_turn_input_event"] = on_turn_input_event
    if on_turn_stream_end is not None:
        reentry_kwargs["on_turn_stream_end"] = on_turn_stream_end
    if on_turn_interrupted is not None:
        reentry_kwargs["on_turn_interrupted"] = on_turn_interrupted
    if on_retry_state is not None:
        reentry_kwargs["on_retry_state"] = on_retry_state

    started_at = time.perf_counter()

    event_count: int = 0

    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    if not isinstance(turn_execution, TurnExecution):
        raise TypeError("turn_execution is required")

    turn_context = turn_execution.context
    hook_scope   = turn_execution.hook_scope
    message      = turn_execution.message

    if on_retry_state is None and turn_context.agent.depth == 0:
        on_retry_state = mind.frontend.runtime.set_wait_retry_state

    if on_turn_input_context is not None:
        on_turn_input_context(turn_context)

    kwargs["turn_id"]     = turn_context.turn_id
    kwargs["permissions"] = turn_context.permissions

    metadata = dict(turn_execution.metadata)
    kwargs["metadata"] = metadata

    if turn_execution.additional_context:
        kwargs["additional_context"] = list(
            turn_execution.additional_context
        )
    if turn_execution.system_message:
        kwargs["system_message"] = turn_execution.system_message

    if ev_report:
        ev_report.begin_turn(turn_context.turn_id)

    if not isinstance(kwargs.get("exec_env"), dict):
        service_env = (
            mind.service_exec_env_snapshot()
            if mind.is_service_mcp_linked()
            else None
        )
        kwargs["exec_env"] = build_runtime_exec_env(service_exec_env=service_env)

    request_skills = kwargs.get("skills")
    if request_skills is None:
        try:
            skill_config = mind.config_session.load()
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "skills.config.failed",
                error,
                level="WARNING",
            )
            skill_config = {}
        kwargs["skills"] = skills_payload(skill_config)

    session_factory_value = kwargs.pop("session_factory", None)
    if session_factory_value is None:
        frontend = getattr(mind, "frontend", None)
        session_factory_value = getattr(frontend, "session_factory", None)
    session_factory = _resolve_output_session_factory(session_factory_value)
    reentry_kwargs["session_factory"] = session_factory

    output_session: OutputSession = session_factory(
        turn_context.output_record_path,
        animate=bool(getattr(mind, "animate", True)),
    )

    output_control: OutputControlPort = output_session.control

    status_control = output_session.status
    presentation   = output_session.presentation
    content        = output_session.content

    if output_session.show_hook_lifecycle:
        hook_scope = hook_scope.with_default_status_port(
            HookPresentationAdapter(presentation)
        )

    interrupted: bool     = False
    first_frame: bool     = True
    turn_completed: bool  = False
    turn_incomplete: bool = False
    turn_failed: bool     = False

    reconciliation_required: bool = False

    turn_usage: dict[str, typing.Any]         = {}
    turn_terminal_meta: dict[str, typing.Any] = {}
    turn_can_continue: bool                   = False

    failure_error: str | None = None
    result_status: RunStatus  = "incomplete"

    result_additional_context: tuple[str, ...] = ()

    failed_tool_context: list[str] = []

    approval_ledger = getattr(mind, "approval_call_ledger", None)
    if not isinstance(approval_ledger, ApprovalCallLedger):
        approval_ledger = ApprovalCallLedger()
        setattr(mind, "approval_call_ledger", approval_ledger)

    async def post_client_tool_result(
        cid: str,
        sid: str,
        call_id: str,
        name: str,
        ok: bool,
        result: typing.Any,
        additional_context: typing.Sequence[str] = (),
        arguments: typing.Mapping[str, typing.Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        """保存工具结果并在服务端确认后收束本地结果状态。"""
        approval_ledger.record_result_pending(
            cid=cid,
            sid=sid,
            turn_id=turn_context.turn_id,
            call_id=call_id,
            name=name,
            ok=ok,
            result=result,
            arguments=arguments,
            additional_context=additional_context,
        )
        await post_tool_result(
            cid,
            sid,
            call_id,
            name,
            ok,
            result,
            additional_context=additional_context,
            arguments=arguments,
            request_id=request_id,
        )
        approval_ledger.mark_result_committed(
            cid=cid,
            sid=sid,
            turn_id=turn_context.turn_id,
            call_id=call_id,
        )

    prompt_blocked: bool = False

    turn_hook_events: TurnHookEvents | None = None

    stop_decision = StopHookDecision.stop()
    event_stream  = None

    tracker: SegmentTracker  = SegmentTracker()

    transcript = mind.transcripts.writer(
        turn_context.transcript_path,
        session_id=turn_context.sid,
        turn_id=turn_context.turn_id,
    )

    def record_pending_assistant_output(*, complete_only: bool = False) -> None:
        """按稳定 item 身份把助手输出写入会话记录。"""
        for item_identity, item_id, assistant_output in tracker.drain_assistant_outputs(
            complete_only=complete_only,
        ):
            if not assistant_output:
                continue
            item_epoch, item_round, item_attempt = item_identity
            payload = {
                "content": assistant_output,
                "item_id": item_id,
                "presentation_epoch": item_epoch,
                "round": item_round,
                "attempt": item_attempt,
            }
            transcript.append(
                "message.created",
                actor="assistant",
                payload=payload,
            )

    idle_wait = IdleStatusTimer(
        lambda: status_control.begin_reply_wait_status(delay_sec=0.0), delay_sec=0.9
    )

    retrying_status = _RetryingStatus(on_retry_state)

    try:
        transcript.open()
        record_turn_started(transcript, turn_execution)

        await output_control.open()

        observe(
            "stream.start",
            cid=metadata.get("cid"),
            sid=metadata.get("sid"),
            turn_id=turn_context.turn_id,
            agent_id=turn_context.agent.agent_id,
            tools=len(tools),
            skills=len(kwargs.get("skills") or []),
        )

        await presentation.emit(build_run_started_view(
            metadata=metadata,
            message=message,
            pref_config=pref_config,
            workdir=str(getattr(mind, "history_workspace", "") or ""),
            permissions=kwargs["permissions"],
            turn_id=str(kwargs.get("turn_id") or ""),
            hook_warnings=(
                getattr(mind, "hook_startup_warnings", ())
                if turn_context.agent.depth == 0
                and turn_context.session_started
                else ()
            ),
        ))

        tool_call_coordinator = ToolCallCoordinator(
            hook_scope,
            transcript=transcript,
            command_sessions=getattr(mind, "command_hook_sessions", None),
            failure_context_sink=failed_tool_context.extend,
        )

        turn_hook_events = TurnHookEvents(hook_scope)

        begin_result = await turn_hook_events.begin(message)
        if begin_result.message != message:
            transcript.append(
                "message.updated",
                actor="user",
                payload={"content": begin_result.message, "source": "hook"},
            )

        message = begin_result.message

        _extend_request_context(
            kwargs,
            additional_context=begin_result.additional_context,
        )

        async def interrupt_nested_turn(call_id: str) -> bool:
            """中断由嵌套本地工具审批取消的当前轮次。"""
            return await _interrupt_approval_cancelled_turn(
                cid=turn_context.cid,
                sid=turn_context.sid,
                turn_id=turn_context.turn_id,
                call_id=call_id,
            )

        client_tool_runner = ClientToolCallRunner(
            session=session,
            output_control=output_control,
            status_control=status_control,
            presentation=presentation,
            tools=tools,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
            patch_preview=getattr(
                getattr(mind, "native_coding", None),
                "preview_patch",
                None,
            ),
            interrupt_turn=interrupt_nested_turn,
        )
        plan_tool_runner = PlanToolCallRunner(
            session=session,
            output_control=output_control,
            status_control=status_control,
            presentation=presentation,
            tools=tools,
            turn_context=turn_context,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
        )

        event_stream = stream_chat(
            pref_config,
            message,
            tools,
            on_reconnect_status=retrying_status.set_transport,
            **kwargs,
        )

        async for event in event_stream:
            event_count += 1
            await idle_wait.cancel()

            if ev_report:
                ev_report.bind_event(event)

            if first_frame:
                observe(
                    "stream.first_event",
                    event_type=event.type,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )
                if (
                    turn_context.agent.depth == 0
                    and not mind.frontend.runtime.active
                ):
                    await mind.stop_anim("wait")
                first_frame = False

            event_type = event.type

            if not isinstance(event, TurnRetryingEvent):
                retrying_status.set_provider(False)

            if is_assistant_output_boundary(event):
                record_pending_assistant_output()
                await content.emit(AssistantOutputBoundary())

            if event_type == "turn.start":
                if on_turn_input_event is not None:
                    on_turn_input_event(event)
                continue

            if event_type == "turn.thinking":
                await status_control.begin_reply_wait_status()
                continue

            if isinstance(event, TurnRetryingEvent):
                retrying_status.set_provider(True)
                record_pending_assistant_output()

                had_assistant_output = tracker.on_turn_retrying(event)

                if had_assistant_output:
                    superseded_payload = {
                        "scope": "response",
                        "presentation_epoch": event.presentation_epoch,
                        "round": event.round,
                        "attempt": event.attempt,
                        "reason": event.reason,
                    }
                    if event.supersedes_item_id:
                        superseded_payload["supersedes_item_id"] = (
                            event.supersedes_item_id
                        )
                    transcript.append(
                        "message.superseded",
                        actor="assistant",
                        payload=superseded_payload,
                    )
                    await content.emit(AssistantResponseSuperseded(
                        turn_id=event.turn_id,
                        presentation_epoch=event.presentation_epoch,
                        round=event.round,
                        attempt=event.attempt,
                        item_id=event.supersedes_item_id,
                    ))
                await status_control.begin_reply_wait_status()
                continue

            if isinstance(event, TurnFailedEvent):

                turn_failed        = True
                failure_error      = event.error
                turn_usage         = dict(event.usage)
                turn_terminal_meta = _terminal_result_fields(event)

                observe(
                    "stream.turn_failed",
                    level="ERROR",
                    error=failure_error,
                    stop_reason=event.stop_reason,
                )
                if turn_context.agent.depth == 0:
                    await mind.await_cleanup(
                        mind.stop_anim("wait", settle=False)
                    )
                await finish_failure(
                    status_control,
                    presentation,
                    None,
                    phase="turn.failed",
                    error=failure_error,
                    usage=turn_usage,
                    terminal_meta=turn_terminal_meta,
                )
                continue

            if isinstance(event, TurnReconciliationRequiredEvent):
                try:
                    recovered = await client_tool_runner.reconcile_known_effect(
                        event.effect_id
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as recovery_error:
                    recovered = False
                    observe(
                        "stream.reconciliation_auto_recovery_failed",
                        level="WARNING",
                        turn_id=turn_context.turn_id,
                        effect_id=event.effect_id,
                        error=f"{type(recovery_error).__name__}: {recovery_error}",
                    )
                if recovered:
                    observe(
                        "stream.reconciliation_auto_recovered",
                        turn_id=turn_context.turn_id,
                        effect_id=event.effect_id,
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                cancelled = await _cancel_reconciliation_turn(
                    cid=str(metadata.get("cid") or ""),
                    sid=str(metadata.get("sid") or ""),
                    turn_id=turn_context.turn_id,
                    effect_id=event.effect_id,
                )

                reconciliation_required: bool = True

                failure_error = event.error or "effect outcome requires reconciliation"

                if not cancelled:
                    failure_error = (
                        f"{failure_error}; failed to release the suspended turn"
                    )

                observe(
                    "stream.reconciliation_required",
                    level="ERROR",
                    turn_id=turn_context.turn_id,
                    effect_id=event.effect_id,
                    error=failure_error,
                    turn_released=cancelled,
                )
                if turn_context.agent.depth == 0:
                    await mind.await_cleanup(
                        mind.stop_anim("wait", settle=False)
                    )
                await finish_failure(
                    status_control,
                    presentation,
                    None,
                    phase="turn.reconciliation_required",
                    error=failure_error,
                )
                break

            if isinstance(event, TextDeltaEvent):
                event_identity = tracker.response_identity(event)
                if tracker.should_ignore_item(
                    event.item_id,
                    identity=event_identity,
                ):
                    continue

                identity     = _response_identity(event, tracker)
                item_changed = tracker.on_text_delta(event)

                if item_changed:
                    tracker.defer_current_output()
                    record_pending_assistant_output(complete_only=True)
                    tracker.remember_current_output()
                    await content.emit(AssistantOutputBoundary())
                await content.emit(AssistantTextDelta(
                    event.text,
                    identity,
                    item_id=event.item_id,
                ))
                idle_wait.reschedule()
                continue

            if isinstance(event, PresentationSupersededEvent):
                record_pending_assistant_output()
                transcript.append(
                    "message.superseded",
                    actor="assistant",
                    payload={
                        "scope": "presentation",
                        "presentation_epoch": event.superseded_epoch,
                        "superseded_by_epoch": event.presentation_epoch,
                        "reason": event.reason,
                    },
                )
                tracker.on_presentation_superseded(event)
                await content.emit(AssistantPresentationSuperseded(
                    turn_id=event.turn_id,
                    superseded_epoch=event.superseded_epoch,
                    presentation_epoch=event.presentation_epoch,
                ))
                continue

            if isinstance(event, TextDoneEvent):
                event_identity = tracker.response_identity(event)
                if tracker.should_ignore_item(
                    event.item_id,
                    identity=event_identity,
                ):
                    continue

                identity           = _response_identity(event, tracker)
                output_was_drained = tracker.was_output_drained(event.item_id)

                tracker.on_text_done(event)

                if output_was_drained and event.final_text is not None:
                    epoch    = identity.presentation_epoch
                    round_no = identity.round
                    attempt  = identity.attempt

                    transcript.append(
                        "message.updated",
                        actor="assistant",
                        payload={
                            "content": event.final_text,
                            "item_id": event.item_id,
                            "presentation_epoch": epoch,
                            "round": round_no,
                            "attempt": attempt,
                        },
                    )
                await content.emit(AssistantSegmentCompleted(
                    identity,
                    final_text=event.final_text,
                    item_id=event.item_id,
                ))
                await status_control.begin_reply_wait_status()
                continue

            if isinstance(event, TextMetaEvent):
                tracker.on_text_meta(event)
                continue

            if isinstance(event, TurnDoneEvent):

                turn_usage         = dict(event.usage)
                turn_terminal_meta = _terminal_result_fields(event)
                turn_can_continue  = event.can_continue is True

                if event.status == "interrupted":
                    interrupted = True
                    if on_turn_interrupted is not None:
                        on_turn_interrupted()
                elif event.status == "completed":
                    turn_completed = True
                else:
                    turn_incomplete = True
                    failure_error = event.reason or None

                if turn_context.agent.depth == 0:
                    await mind.await_cleanup(
                        mind.stop_anim("wait", settle=False)
                    )
                await status_control.end_status(immediate=True)

                continue

            if isinstance(event, (TurnInputAcceptedEvent, TurnLogicalSettledEvent)):
                if on_turn_input_event is not None:
                    accepted_input = on_turn_input_event(event)
                    if (
                        isinstance(event, TurnInputAcceptedEvent)
                        and isinstance(accepted_input, TurnInput)
                    ):
                        transcript.append(
                            "message.created",
                            actor="user",
                            payload=build_turn_input_payload(
                                accepted_input.text,
                                attachments=accepted_input.attachments,
                                extras=accepted_input.extras,
                            ),
                        )
                continue

            if event_type == "tool.builtin.call":
                await status_control.begin_tool_status()
                continue

            if isinstance(event, ToolBuiltinDoneEvent):
                tracker.on_builtin_done(event)
                await status_control.end_status()
                continue

            if event_type == "tool.calls.start":
                await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if event_type == "tool.calls.done":
                await status_control.begin_reply_wait_status(delay_sec=0.75)
                continue

            if isinstance(event, ToolApprovalRequiredEvent):
                approval = approval_from_event(event)
                if turn_context.agent.depth > 0:
                    approval["agent_id"] = turn_context.agent.agent_id
                    approval["agent_type"] = turn_context.agent.agent_type
                    approval["agent_depth"] = turn_context.agent.depth
                await status_control.end_status(immediate=True)

                approval_started_at = time.perf_counter()
                approval_id = approval_id_from_event(event)
                approval_tool = str(approval.get("tool") or "").strip()
                approval_call_id = event.call_id

                permission_decision = None
                approval_invocation: ToolInvocation | None = None
                if approval_tool:
                    raw_arguments = approval.get("arguments")
                    approval_arguments = (
                        dict(raw_arguments)
                        if isinstance(raw_arguments, dict)
                        else {}
                    )
                    if (
                        not approval_arguments
                        and approval_tool in LOCAL_EXEC_POLICY_TOOLS
                    ):
                        command = approval.get("command")
                        argument_name = (
                            "stdin"
                            if approval_tool == "write_stdin"
                            else "command"
                        )
                        if isinstance(command, str) and command.strip():
                            approval_arguments[argument_name] = command

                    approval_invocation = _tool_invocation_from_event(
                        turn_context,
                        event,
                        tools,
                        arguments=approval_arguments,
                        name=approval_tool,
                    )
                    permission_decision = (
                        await tool_call_coordinator.prepare_permission(
                            approval_invocation
                        )
                    )
                    effective_arguments = None
                    if permission_decision.updated_input is not None:
                        effective_arguments = dict(
                            tool_call_coordinator.effective_invocation(
                                approval_invocation,
                                permission_decision,
                            ).arguments
                        )
                    approval = _approval_with_updated_input(
                        approval,
                        approval_tool,
                        effective_arguments,
                    )

                observe(
                    "approval.requested",
                    tool=approval_tool,
                    call_id=approval_call_id,
                    approval_id=approval_id,
                )

                decision: ApprovalDecisionValue
                decision_source: ApprovalSource
                if (
                    permission_decision is not None
                    and permission_decision.action == "deny"
                ):
                    decision, decision_source = "decline", "hook"
                elif (
                    permission_decision is not None
                    and permission_decision.action == "allow"
                ):
                    decision, decision_source = "accept", "hook"
                elif kwargs["permissions"].approval_policy == "never":
                    decision, decision_source = "decline", "policy"
                else:
                    outcome = await mind.approval_coordinator.request_outcome(
                        approval
                    )
                    decision, decision_source = outcome.decision, outcome.source

                allowed_decisions = approval_decisions(approval)
                if decision != "cancel" and decision not in allowed_decisions:
                    raise RuntimeError(
                        f"approval decision is not available: {decision}"
                    )

                observe(
                    "approval.decided",
                    tool=approval_tool,
                    call_id=approval_call_id,
                    approval_id=approval_id,
                    decision=decision,
                    decision_source=decision_source,
                    hook_keys=(
                        list(permission_decision.hook_keys)
                        if permission_decision is not None
                        else []
                    ),
                    elapsed_ms=int(
                        (time.perf_counter() - approval_started_at) * 1000
                    ),
                )

                await presentation.emit(build_approval_view(
                    approval,
                    decision=decision,
                    source=decision_source,
                ))
                approval_turn_id = str(
                    approval.get("turn_id") or turn_context.turn_id
                ).strip()
                try:
                    await post_tool_approval(
                        turn_context.cid,
                        turn_context.sid,
                        approval_call_id,
                        approval_id,
                        **_approval_report_kwargs(
                            approval,
                            decision=decision,
                            source=decision_source,
                            turn_id=approval_turn_id,
                            hook_reason=(
                                permission_decision.reason
                                if permission_decision is not None
                                else ""
                            ),
                            additional_context=(
                                permission_decision.additional_context
                                if permission_decision is not None
                                else ()
                            ),
                        ),
                    )
                except Exception as error:
                    approval_ledger.discard(
                        cid=turn_context.cid,
                        sid=turn_context.sid,
                        turn_id=turn_context.turn_id,
                        call_id=approval_call_id,
                    )
                    observe_exception(
                        "approval.report_failed",
                        error,
                        tool=approval_tool,
                        call_id=approval_call_id,
                        approval_id=approval_id,
                        decision=decision,
                    )
                    raise

                approved = decision in TOOL_APPROVAL_ACCEPT_DECISIONS
                if approved:
                    if (
                        approval_invocation is not None
                        and approval_tool in LOCAL_EXEC_POLICY_TOOLS
                        and decision in {
                            "acceptForSession",
                            "acceptWithExecpolicyAmendment",
                        }
                    ):
                        local_policy_update_error = _apply_local_exec_policy_approval(
                            mind.exec_policy_manager,
                            invocation=approval_invocation,
                            approval=approval,
                            decision=decision,
                        )
                        if local_policy_update_error:
                            observe(
                                "approval.local_policy_update_failed",
                                level="WARNING",
                                call_id=approval_call_id,
                                decision=decision,
                                error=local_policy_update_error,
                            )
                    approval_ledger.record_approved(
                        cid=turn_context.cid,
                        sid=turn_context.sid,
                        turn_id=turn_context.turn_id,
                        call_id=approval_call_id,
                    )
                else:
                    approval_ledger.discard(
                        cid=turn_context.cid,
                        sid=turn_context.sid,
                        turn_id=turn_context.turn_id,
                        call_id=approval_call_id,
                    )
                if not approved:
                    await status_control.begin_reply_wait_status(
                        delay_sec=0.15,
                        animate_after_sec=0.85,
                    )
                continue

            if isinstance(event, ToolCallEvent):
                name      = event.name
                arguments = dict(event.arguments)

                approval_state = approval_ledger.consume(
                    cid=turn_context.cid,
                    sid=turn_context.sid,
                    turn_id=turn_context.turn_id,
                    call_id=event.call_id,
                )
                if approval_state == "consumed":
                    result_record = approval_ledger.result_for(
                        cid=turn_context.cid,
                        sid=turn_context.sid,
                        turn_id=turn_context.turn_id,
                        call_id=event.call_id,
                    )
                    if result_record is not None and result_record.state == "pending":
                        await post_client_tool_result(
                            turn_context.cid,
                            turn_context.sid,
                            event.call_id,
                            result_record.name,
                            result_record.ok,
                            result_record.result,
                            additional_context=result_record.additional_context,
                            arguments=result_record.arguments,
                        )
                    observe(
                        "tool.call.duplicate",
                        call_id=event.call_id,
                        turn_id=turn_context.turn_id,
                    )
                    continue

                approval_consumed = approval_state == "approved"

                if not name:
                    await post_client_tool_result(
                        turn_context.cid,
                        turn_context.sid,
                        event.call_id,
                        "",
                        False,
                        {"error": "tool.call missing name/tool"},
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                invocation = _tool_invocation_from_event(
                    turn_context,
                    event,
                    tools,
                    arguments=arguments,
                )

                hook_decision = await tool_call_coordinator.prepare(invocation)

                if not hook_decision.allowed:
                    tool_call_coordinator.record_rejected(
                        invocation,
                        hook_decision.reason,
                    )
                    await post_client_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        False,
                        _hook_denied_result(hook_decision.reason),
                        additional_context=hook_decision.additional_context,
                        arguments=invocation.arguments,
                    )
                    await status_control.begin_reply_wait_status(delay_sec=0.15)
                    continue

                invocation = tool_call_coordinator.effective_invocation(
                    invocation,
                    hook_decision,
                )
                arguments = dict(invocation.arguments)

                if name == PLAN_STEPS_TOOL:
                    tool_outcome = await client_tool_runner.execute(
                        invocation,
                        use_coding_trace=False,
                        operation_handler=plan_tool_runner.execute_operation,
                    )
                    tool_result = tool_outcome.result
                    await post_client_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        tool_result.name,
                        tool_result.ok,
                        tool_result.fields,
                        arguments=invocation.arguments,
                        additional_context=tool_outcome.additional_context,
                    )
                    await status_control.begin_reply_wait_status(delay_sec=0.75)
                    continue

                local_policy_requirement = _local_exec_policy_requirement(
                    mind.exec_policy_manager,
                    turn_context,
                    tool=name,
                    arguments=arguments,
                    call_id=event.call_id,
                )
                if (
                    local_policy_requirement is not None
                    and local_policy_requirement.state == "forbidden"
                ):
                    tool_call_coordinator.record_rejected(
                        invocation,
                        "local execution policy forbids command",
                        result=_local_exec_policy_denied_result(local_policy_requirement),
                    )
                    await post_client_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        False,
                        _local_exec_policy_denied_result(local_policy_requirement),
                        arguments=invocation.arguments,
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                if (
                    local_policy_requirement is not None
                    and local_policy_requirement.state == "needs_approval"
                    and not approval_consumed
                ):
                    approval_reason = str(
                        invocation.arguments.get("justification")
                        or invocation.reason
                        or ""
                    ).strip()
                    requested_sandbox_permissions = str(
                        invocation.arguments.get("sandbox_permissions") or ""
                    ).strip().casefold()
                    if (
                        name in {"shell_command", "exec_command"}
                        and not approval_reason
                        and requested_sandbox_permissions != "require_escalated"
                    ):
                        missing_reason = {
                            "execution_denied": True,
                            "error": "shell approval reason is missing",
                        }
                        tool_call_coordinator.record_rejected(
                            invocation,
                            "shell approval reason is missing",
                            result=missing_reason,
                        )
                        await post_client_tool_result(
                            invocation.turn.cid,
                            invocation.turn.sid,
                            invocation.call_id,
                            invocation.name,
                            False,
                            missing_reason,
                            arguments=invocation.arguments,
                        )
                        await status_control.begin_reply_wait_status()
                        continue
                    if kwargs["permissions"].approval_policy == "never":
                        local_policy_result = _local_exec_policy_denied_result(
                            local_policy_requirement
                        )
                        tool_call_coordinator.record_rejected(
                            invocation,
                            "local execution policy requires approval",
                            result=local_policy_result,
                        )
                        await post_client_tool_result(
                            invocation.turn.cid,
                            invocation.turn.sid,
                            invocation.call_id,
                            invocation.name,
                            False,
                            local_policy_result,
                            arguments=invocation.arguments,
                        )
                        await status_control.begin_reply_wait_status()
                        continue

                    local_approval = _local_exec_policy_approval(
                        invocation=invocation,
                        requirement=local_policy_requirement,
                    )
                    local_outcome = await mind.approval_coordinator.request_outcome(
                        local_approval
                    )
                    await presentation.emit(build_approval_view(
                        local_approval,
                        decision=local_outcome.decision,
                        source=local_outcome.source,
                    ))
                    if local_outcome.decision not in TOOL_APPROVAL_ACCEPT_DECISIONS:
                        local_policy_result = (
                            _local_exec_policy_cancelled_result()
                            if local_outcome.decision == "cancel"
                            else {
                                "approval_denied": True,
                                "error": "local execution policy approval declined",
                            }
                        )
                        tool_call_coordinator.record_rejected(
                            invocation,
                            "local execution policy approval declined",
                            result=local_policy_result,
                        )
                        await post_client_tool_result(
                            invocation.turn.cid,
                            invocation.turn.sid,
                            invocation.call_id,
                            invocation.name,
                            False,
                            local_policy_result,
                            arguments=invocation.arguments,
                        )
                        if local_outcome.decision == "cancel":
                            interrupted = await _interrupt_approval_cancelled_turn(
                                cid=turn_context.cid,
                                sid=turn_context.sid,
                                turn_id=turn_context.turn_id,
                                call_id=invocation.call_id,
                            )
                            if not interrupted:
                                raise TurnControlRequestError(
                                    "failed to interrupt turn after approval cancellation"
                                )
                            break
                        await status_control.begin_reply_wait_status()
                        continue

                    policy_update_error = _apply_local_exec_policy_approval(
                        mind.exec_policy_manager,
                        invocation=invocation,
                        approval=local_approval,
                        decision=local_outcome.decision,
                    )
                    if policy_update_error:
                        local_policy_result = {
                            "approval_denied": True,
                            "error": "local execution policy update failed",
                            "detail": policy_update_error,
                        }
                        tool_call_coordinator.record_rejected(
                            invocation,
                            "local execution policy update failed",
                            result=local_policy_result,
                        )
                        await post_client_tool_result(
                            invocation.turn.cid,
                            invocation.turn.sid,
                            invocation.call_id,
                            invocation.name,
                            False,
                            local_policy_result,
                            arguments=invocation.arguments,
                        )
                        await status_control.begin_reply_wait_status()
                        continue

                use_coding_trace = coding_trace_tool(name)

                try:
                    tool_outcome = await client_tool_runner.execute(
                        invocation,
                        use_coding_trace=use_coding_trace,
                    )
                except TurnControlRequestError as error:
                    interrupted = True
                    failure_error = str(error)
                    break
                tool_result = tool_outcome.result
                await post_client_tool_result(
                    invocation.turn.cid,
                    invocation.turn.sid,
                    invocation.call_id,
                    tool_result.name,
                    tool_result.ok,
                    tool_result.fields,
                    arguments=invocation.arguments,
                    additional_context=tool_outcome.additional_context,
                )
                await status_control.begin_reply_wait_status(delay_sec=0.75)
                continue

            if isinstance(event, ToolOutputEvent):
                name = event.name
                if not name:
                    continue

                arguments = dict(event.arguments)

                use_coding_trace = coding_trace_tool(name)
                tool_run         = server_tool_output_result(event.payload)

                transcript.append(
                    (
                        "tool.failed"
                        if tool_run.status == "failed"
                        else "tool.completed"
                    ),
                    actor="tool",
                    payload={
                        "call_id": event.call_id,
                        "name": name,
                        "arguments": arguments,
                        "ok": tool_run.ok,
                        "status": tool_run.status,
                        "duration_ms": tool_run.cost_ms,
                        "result": tool_run.fields,
                    },
                )

                if use_coding_trace:
                    await status_control.end_status()

                if tool_run.status not in {"declined", "cancelled"}:
                    await show_tool_result(
                        presentation,
                        name,
                        arguments,
                        tool_run,
                        use_coding_trace=use_coding_trace,
                        call_id=event.call_id,
                    )

                await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if await handle_lifecycle_event(
                event,
                presentation=presentation,
                status_control=status_control,
            ):
                continue

            continue

    except LocalEffectReconciliationRequired as error:
        result_status = "reconciliation_required"
        failure_error = str(error)
        observe(
            "stream.local_effect_reconciliation_required",
            level="ERROR",
            turn_id=turn_context.turn_id,
            effect_id=error.effect_id,
        )
        if turn_context.agent.depth == 0:
            await mind.await_cleanup(mind.stop_anim("wait"))
        await finish_failure(
            status_control,
            presentation,
            ev_report,
            phase="turn.reconciliation_required",
            error=failure_error,
        )

    except PromptHookBlockedError as error:
        result_status = "failed"
        failure_error = str(error)
        result_additional_context = error.additional_context
        prompt_blocked = True

        if result_additional_context and turn_context.agent.depth == 0:
            mind.conversation.queue_turn_context(result_additional_context)

        observe(
            "stream.prompt_blocked",
            level="WARNING",
            turn_id=turn_context.turn_id,
        )

        await finish_failure(
            status_control,
            presentation,
            ev_report,
            phase="turn.prompt_blocked",
            error=failure_error,
        )

    except asyncio.CancelledError:
        interrupted = True
        if failed_tool_context and turn_context.agent.depth == 0:
            mind.conversation.queue_turn_context(failed_tool_context)
        observe(
            "stream.interrupted",
            level="WARNING",
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise

    except Exception as e:
        result_status = "failed"
        result_additional_context = tuple(failed_tool_context)
        if result_additional_context and turn_context.agent.depth == 0:
            mind.conversation.queue_turn_context(result_additional_context)
        observe_exception(
            "stream.failed",
            e,
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        failure_error = friendly_exception_text(e)

        if turn_context.agent.depth == 0:
            await mind.await_cleanup(mind.stop_anim("wait"))

        await finish_failure(
            status_control,
            presentation,
            ev_report,
            phase="turn.failed",
            error=failure_error
        )

    else:
        if interrupted:
            result_status = "interrupted"
        elif reconciliation_required:
            result_status = "reconciliation_required"
        elif turn_failed:
            result_status = "failed"
        elif turn_completed:
            result_status = "completed"
        elif turn_incomplete:
            result_status = "incomplete"
        else:
            failure_error = "stream ended before turn completion"
            await finish_failure(
                status_control,
                presentation,
                ev_report,
                phase="turn.incomplete",
                error=failure_error,
            )

        if turn_completed and turn_context.agent.depth == 0:
            record_pending_assistant_output()
            mind.remember_last_assistant_reply(tracker.assistant_text())

        await status_control.end_status()
        await content.emit(SourcesOutput(tuple(tracker.iter_sources())))

        if turn_completed and not turn_failed:
            await presentation.emit(build_run_completed_view(
                turn_usage,
                turn_terminal_meta,
            ))
        elif turn_incomplete:
            await presentation.emit(build_run_incomplete_view(
                turn_usage,
                reason=failure_error,
                can_continue=turn_can_continue,
                terminal_meta=turn_terminal_meta,
            ))

        observe(
            "stream.complete",
            outcome=(
                "interrupted"
                if interrupted
                else "failed"
                if turn_failed
                else "reconciliation_required"
                if reconciliation_required
                else "incomplete"
                if turn_incomplete
                else "complete"
                if turn_completed
                else "incomplete"
            ),
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            usage=turn_usage or None,
        )

    finally:
        approval_ledger.clear_turn(
            cid=turn_context.cid,
            sid=turn_context.sid,
            turn_id=turn_context.turn_id,
            preserve_pending_results=result_status not in {"completed", "interrupted"},
        )
        retrying_status.close()

        if on_turn_stream_end is not None and event_stream is not None:
            on_turn_stream_end(
                getattr(event_stream, "end_reason", None) or "cancelled"
            )

        record_pending_assistant_output()

        record_turn_finished(
            transcript,
            status="interrupted" if interrupted else result_status,
            usage=turn_usage,
            error=failure_error,
            terminal_meta=turn_terminal_meta,
        )

        if turn_hook_events is not None and not prompt_blocked:
            stop_outcome = "interrupted" if interrupted else result_status
            try:
                if interrupted:
                    await mind.await_cleanup(_discard_stop_hook_decision(
                        turn_hook_events.stop(
                            outcome=stop_outcome,
                            error=failure_error,
                            usage=turn_usage,
                            last_assistant_message=(
                                tracker.assistant_text()
                            ),
                            continuation_count=turn_continuation_count(
                                turn_execution
                            ),
                        )
                    ))
                else:
                    stop_decision = await turn_hook_events.stop(
                        outcome=stop_outcome,
                        error=failure_error,
                        usage=turn_usage,
                        last_assistant_message=(
                            tracker.assistant_text()
                        ),
                        continuation_count=turn_continuation_count(
                            turn_execution
                        ),
                    )
            except Exception as error:
                observe_exception(
                    "hooks.stop.failed",
                    error,
                    level="WARNING",
                    turn_id=turn_context.turn_id,
                )

        transcript.close()

        await idle_wait.cancel()
        await mind.await_cleanup(output_control.stop(blink=not interrupted))

    result = RunResult(
        status=result_status,
        assistant_text=tracker.assistant_text(),
        usage=dict(turn_usage),
        error=failure_error,
        additional_context=result_additional_context,
        **turn_terminal_meta,
    )

    continuation_allowed = (
        result_status == "completed"
        or (
            result_status == "incomplete"
            and turn_can_continue
        )
    )

    if stop_decision.should_continue and continuation_allowed:
        continuation_count = turn_continuation_count(turn_execution)
        if continuation_count >= MAX_STOP_CONTINUATIONS:
            observe(
                "hooks.stop.limit_reached",
                level="WARNING",
                turn_id=turn_context.turn_id,
                continuation_count=continuation_count,
                hook_keys=list(stop_decision.hook_keys),
            )
            return result
        return await stream_turn(
            mind,
            session,
            pref_config,
            tools,
            turn_execution=create_continuation_execution(
                turn_execution,
                stop_decision.continuation_prompt,
                additional_context=stop_decision.additional_context,
            ),
            **reentry_kwargs,
        )

    if stop_decision.should_continue:
        observe(
            "hooks.stop.continuation_denied",
            level="WARNING",
            turn_id=turn_context.turn_id,
            outcome=result_status,
            can_continue=turn_can_continue,
            stop_reason=turn_terminal_meta.get("stop_reason"),
        )

    return result


if __name__ == '__main__':
    pass
