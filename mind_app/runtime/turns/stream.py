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
    ApprovalStore,
    approval_execpolicy_amendment,
    approval_from_event,
    approval_id_from_event,
    validate_tool_approval
)
from mind_app.approval.models import ApprovalDecisionValue
from mind_nova.events import EventReport
from mind_nova.requests.chat import stream_chat
from mind_nova.turn_inputs import TurnInput
from mind_nova.stream_events import (
    TextDeltaEvent,
    TextDoneEvent,
    TextMetaEvent,
    ToolApprovalRequiredEvent,
    ToolBuiltinDoneEvent,
    ToolCallEvent,
    ToolEvent,
    ToolOutputEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
    TurnTerminalEvent
)
from mind_nova.requests.tools import (
    ToolApprovalExpired,
    post_tool_approval,
    post_tool_result
)
from ...output import (
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    OutputControlPort,
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
from ..hooks.models import (
    ToolOperationResult,
    ToolResultSnapshot
)
from ..hooks.turn import (
    PromptHookBlockedError,
    TurnHookEvents
)
from ..environment.exec_env import build_runtime_exec_env
from ..support.session_policy import friendly_exception_text
from ..tools.run import server_tool_output_result
from ..tools.display import show_tool_result
from ..tools.execution_policy import (
    is_execution_ignored,
    validate_execution_policy
)
from ..tools.client_call import (
    ClientToolCallRunner,
    build_client_tool_post_kwargs
)
from ..tools.plan_call import PlanToolCallRunner
from ..tools.plan_steps import PlanExecutionReport
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


def _terminal_result_fields(
    event: TurnTerminalEvent,
) -> dict[str, typing.Any]:
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
    arguments: dict[str, typing.Any] | None = None
) -> ToolInvocation:
    """从流式事件构建不暴露内部授权字段的工具调用上下文。"""
    local_meta     = meta_for_tool(tools, event.name)
    effective_meta = {**(local_meta or {}), **(event.meta or {})} or None

    return ToolInvocation(
        turn=turn_context,
        call_id=event.call_id,
        name=event.name,
        arguments=dict(event.arguments if arguments is None else arguments),
        meta=effective_meta,
        execution=event.execution,
    )


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


def _extend_request_context(
    kwargs: dict[str, typing.Any],
    *,
    additional_context: typing.Iterable[str] = (),
    system_message: str = "",
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
    awaitable: typing.Awaitable[StopHookDecision],
) -> None:
    """执行停止 Hook 并丢弃清理阶段不应消费的续跑决定。"""
    await awaitable


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
    on_turn_input_context = kwargs.pop("on_turn_input_context", None)
    on_turn_input_event   = kwargs.pop("on_turn_input_event", None)
    on_turn_stream_end    = kwargs.pop("on_turn_stream_end", None)

    reentry_kwargs = dict(kwargs)
    if on_turn_input_context is not None:
        reentry_kwargs["on_turn_input_context"] = on_turn_input_context
    if on_turn_input_event is not None:
        reentry_kwargs["on_turn_input_event"] = on_turn_input_event
    if on_turn_stream_end is not None:
        reentry_kwargs["on_turn_stream_end"] = on_turn_stream_end

    started_at = time.perf_counter()

    event_count: int = 0

    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    if not isinstance(turn_execution, TurnExecution):
        raise TypeError("turn_execution is required")

    turn_context = turn_execution.context
    hook_scope   = turn_execution.hook_scope
    message      = turn_execution.message

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

    session_factory = kwargs.pop("session_factory", None)

    if session_factory is None:
        frontend = getattr(mind, "frontend", None)
        session_factory = getattr(frontend, "session_factory", None)
    if session_factory is None:
        raise RuntimeError("stream output session factory is required")
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

    turn_usage: dict[str, typing.Any] = {}
    turn_terminal_meta: dict[str, typing.Any] = {}
    turn_can_continue: bool = False

    failure_error: str | None = None
    result_status: RunStatus  = "incomplete"

    result_additional_context: tuple[str, ...] = ()

    failed_tool_context: list[str] = []

    prompt_blocked: bool = False

    turn_hook_events: TurnHookEvents | None = None

    stop_decision = StopHookDecision.stop()
    event_stream  = None

    approvals: ApprovalStore = ApprovalStore()
    tracker: SegmentTracker  = SegmentTracker()

    transcript = mind.transcripts.writer(
        turn_context.transcript_path,
        session_id=turn_context.sid,
        turn_id=turn_context.turn_id,
    )

    def record_pending_assistant_output() -> None:
        """把尚未持久化的助手输出块写入当前会话记录。"""
        assistant_output = tracker.commit_assistant_output()
        if assistant_output:
            transcript.append(
                "message.created",
                actor="assistant",
                payload={"content": assistant_output},
            )

    idle_wait = IdleStatusTimer(
        lambda: status_control.begin_reply_wait_status(delay_sec=0.0), delay_sec=0.9
    )

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

        client_tool_runner = ClientToolCallRunner(
            session=session,
            output_control=output_control,
            status_control=status_control,
            presentation=presentation,
            tools=tools,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
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

        event_stream = stream_chat(pref_config, message, tools, **kwargs)

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

            if isinstance(event, TurnFailedEvent):
                turn_failed   = True
                failure_error = event.error
                turn_usage = dict(event.usage)
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

            if isinstance(event, TextDeltaEvent):
                tracker.on_text_delta(event)
                await content.emit(AssistantTextDelta(event.text))
                idle_wait.reschedule()
                continue

            if isinstance(event, TextDoneEvent):
                tracker.on_text_done(event)
                await content.emit(AssistantSegmentCompleted())
                await status_control.begin_reply_wait_status()
                continue

            if isinstance(event, TextMetaEvent):
                tracker.on_text_meta(event)
                continue

            if isinstance(event, TurnDoneEvent):
                turn_usage = dict(event.usage)
                turn_terminal_meta = _terminal_result_fields(event)
                turn_can_continue = event.can_continue is True

                if event.status == "interrupted":
                    interrupted = True
                elif event.status == "completed":
                    turn_completed = True
                else:
                    turn_incomplete = True
                    failure_error = event.reason or None

                if turn_context.agent.depth == 0:
                    await mind.await_cleanup(
                        mind.freeze_anim("wait")
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

                approval_id      = approval_id_from_event(event)
                approval_tool    = event.name
                approval_call_id = event.call_id

                permission_decision = None

                if approval_tool:
                    approval_arguments = dict(event.arguments)
                    if not approval_arguments:
                        raw_approval_arguments = approval.get("arguments")
                        approval_arguments = (
                            dict(raw_approval_arguments)
                            if isinstance(raw_approval_arguments, dict)
                            else {}
                        )

                    approval_invocation = _tool_invocation_from_event(
                        turn_context,
                        event,
                        tools,
                        arguments=approval_arguments,
                    )

                    permission_decision = (
                        await tool_call_coordinator.prepare_permission(
                            approval_invocation
                        )
                    )
                    effective_approval_arguments = None
                    if permission_decision.updated_input is not None:
                        effective_approval_arguments = dict(
                            tool_call_coordinator.effective_invocation(
                                approval_invocation,
                                permission_decision,
                            ).arguments
                        )
                    approval = _approval_with_updated_input(
                        approval,
                        approval_tool,
                        effective_approval_arguments,
                    )

                observe(
                    "approval.requested",
                    tool=approval_tool,
                    call_id=approval_call_id,
                    approval_id=approval_id,
                )

                if (
                    permission_decision is not None
                    and permission_decision.action == "deny"
                ):
                    decision = "decline"
                    decision_source = "hook"
                elif (
                    permission_decision is not None
                    and permission_decision.action == "allow"
                ):
                    decision = "accept"
                    decision_source = "hook"
                elif kwargs["permissions"].approval_policy == "never":
                    decision = "decline"
                    decision_source = "policy"
                else:
                    decision = await mind.approval_coordinator.request(approval)
                    decision_source = mind.approval_coordinator.decision_source

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
                    elapsed_ms=int((time.perf_counter() - approval_started_at) * 1000),
                )

                if decision == "expired":
                    observe(
                        "approval.expired",
                        level="WARNING",
                        tool=approval_tool,
                        call_id=approval_call_id,
                        approval_id=approval_id,
                        source="interaction",
                    )
                    await presentation.emit(build_approval_view(
                        approval,
                        decision=decision,
                        source=decision_source,
                    ))
                    await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                    continue

                approved = decision in TOOL_APPROVAL_ACCEPT_DECISIONS

                approvals.mark_decision(
                    call_id=event.call_id,
                    approval=approval,
                    decision=decision,
                )

                await presentation.emit(build_approval_view(
                    approval,
                    decision=decision,
                    source=decision_source,
                ))
                try:
                    approval_post_kwargs = _approval_report_kwargs(
                        approval,
                        decision=decision,
                        source=decision_source,
                        turn_id=turn_context.turn_id,
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
                    )
                    await post_tool_approval(
                        turn_context.cid,
                        turn_context.sid,
                        event.call_id,
                        approval_id,
                        **approval_post_kwargs,
                    )
                except ToolApprovalExpired:
                    observe(
                        "approval.expired",
                        level="WARNING",
                        tool=approval_tool,
                        call_id=approval_call_id,
                        approval_id=approval_id,
                        source="report",
                    )
                except Exception as error:
                    observe_exception(
                        "approval.report_failed",
                        error,
                        tool=approval_tool,
                        call_id=approval_call_id,
                        approval_id=approval_id,
                        decision=decision,
                    )
                    raise
                if not approved:
                    await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if isinstance(event, ToolCallEvent):
                name      = event.name
                arguments = dict(event.arguments)

                if not name:
                    await post_tool_result(
                        turn_context.cid,
                        turn_context.sid,
                        event.call_id,
                        "",
                        False,
                        {"error": "tool.call missing name/tool"},
                        execution=event.execution,
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
                    await post_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        False,
                        _hook_denied_result(hook_decision.reason),
                        execution=invocation.execution,
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
                    async def execute_plan_call(
                        prepared: ToolInvocation
                    ) -> ToolOperationResult[PlanExecutionReport]:
                        """执行已经获准的计划工具调用。"""
                        report = await plan_tool_runner.handle(
                            invocation=prepared
                        )

                        return ToolOperationResult(
                            value=report,
                            snapshot=ToolResultSnapshot(
                                ok=report.ok,
                                text=report.text,
                                fields=report.fields,
                            ),
                            additional_context=report.additional_context,
                            system_message=report.system_message,
                        )

                    hook_run = await tool_call_coordinator.run_invocation(
                        invocation,
                        execute_plan_call,
                    )
                    if not hook_run.allowed:
                        plan_ok        = False
                        plan_result    = _hook_denied_result(hook_run.reason)
                        visible_result = None
                    else:
                        if hook_run.value is None:
                            raise RuntimeError("plan tool execution returned no result")

                        visible_result = hook_run.visible_result
                        if visible_result is None:
                            raise RuntimeError(
                                "plan tool execution returned no visible result"
                            )

                        plan_ok     = visible_result.ok
                        plan_result = visible_result.fields

                    post_kwargs: dict[str, typing.Any] = {
                        "execution": invocation.execution,
                        "arguments": invocation.arguments,
                    }

                    if (
                        visible_result is not None
                        and visible_result.additional_context
                    ):
                        post_kwargs["additional_context"] = (
                            visible_result.additional_context
                        )
                    elif hook_run.additional_context:
                        post_kwargs["additional_context"] = (
                            hook_run.additional_context
                        )
                    if (
                        visible_result is not None
                        and visible_result.system_message
                    ):
                        post_kwargs["system_message"] = visible_result.system_message

                    await post_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        plan_ok,
                        plan_result,
                        **post_kwargs,
                    )
                    await status_control.begin_reply_wait_status(delay_sec=0.75)
                    continue

                approval_decision = validate_tool_approval(
                    event=event,
                    name=name,
                    arguments=arguments,
                    store=approvals,
                    meta=event.meta,
                    local_meta=meta_for_tool(tools, name),
                    approval_policy=kwargs["permissions"].approval_policy,
                )

                if approval_decision.action == "wait":
                    observe(
                        "approval.enforced",
                        action="wait",
                        tool=name,
                        call_id=event.call_id,
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                if approval_decision.action == "reject":
                    observe(
                        "approval.enforced",
                        level="WARNING",
                        action="reject",
                        tool=name,
                        call_id=event.call_id,
                    )
                    tool_call_coordinator.record_rejected(
                        invocation,
                        "tool approval rejected",
                        result=approval_decision.result or {},
                    )
                    await post_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        False,
                        approval_decision.result or {},
                        execution=invocation.execution,
                        arguments=invocation.arguments,
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                if execution_policy_result := validate_execution_policy(
                    name=name,
                    execution=invocation.execution,
                ):
                    if is_execution_ignored(execution_policy_result):
                        await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                        continue
                    tool_call_coordinator.record_rejected(
                        invocation,
                        "tool execution policy rejected the call",
                        result=execution_policy_result,
                    )
                    await post_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        False,
                        execution_policy_result,
                        execution=invocation.execution,
                        arguments=invocation.arguments,
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                use_coding_trace = coding_trace_tool(name)

                tool_outcome = await client_tool_runner.execute(
                    invocation,
                    use_coding_trace=use_coding_trace,
                )
                tool_result = tool_outcome.result
                post_kwargs = build_client_tool_post_kwargs(
                    tool_outcome,
                    execution=invocation.execution,
                )
                await post_tool_result(
                    invocation.turn.cid,
                    invocation.turn.sid,
                    invocation.call_id,
                    tool_result.name,
                    tool_result.ok,
                    tool_result.fields,
                    arguments=invocation.arguments,
                    **post_kwargs,
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
            mind.remember_last_assistant_reply(tracker.latest_assistant_output_text())

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
        if on_turn_stream_end is not None and event_stream is not None:
            on_turn_stream_end(
                getattr(event_stream, "end_reason", None) or "disconnected"
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
                                tracker.latest_assistant_output_text()
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
                            tracker.latest_assistant_output_text()
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
        assistant_text=tracker.latest_assistant_output_text(),
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
