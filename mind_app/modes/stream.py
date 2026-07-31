# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from mind_app.mcp.contracts import McpSessionLike
from mind_app.mcp.tool_store import meta_for_tool
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_core.skills import skills_payload
from mind_app.approval.policy import (
    ApprovalStore,
    approval_from_event,
    approval_id_from_event,
    validate_tool_approval
)
from mind_nova.events import EventReport
from mind_nova.requests.chat import stream_chat
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
    TurnFailedEvent
)
from mind_nova.requests.tools import (
    ToolApprovalExpired,
    post_tool_approval,
    post_tool_result
)
from ..output import (
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    OutputControlPort,
    SourcesOutput
)
from ..output.session import OutputSession
from .result import (
    RunResult,
    RunStatus
)
from ..presentation.approval_views import build_approval_view
from ..presentation.models import ApprovalSource
from ..presentation.run_views import (
    build_run_completed_view,
    build_run_started_view
)
from ..runtime.support.loop_support import finish_failure
from ..runtime.execution import (
    ToolInvocation,
    TurnContext
)
from ..runtime.hooks.tool import ToolCallCoordinator
from ..runtime.hooks.turn import (
    PromptHookBlockedError,
    TurnHookEvents
)
from ..runtime.environment.exec_env import build_runtime_exec_env
from ..runtime.support.session_policy import friendly_exception_text
from ..runtime.tools.run import server_tool_output_result
from ..runtime.tools.display import show_tool_result
from ..runtime.tools.execution_policy import (
    is_execution_ignored,
    validate_execution_policy
)
from ..runtime.tools.client_call import ClientToolCallRunner
from ..runtime.tools.plan_call import PlanToolCallRunner
from ..runtime.turns.executor import TurnExecution
from ..runtime.support.idle_status import IdleStatusTimer
from ..stream_events.tool_trace import coding_trace_tool
from ..stream_events.lifecycle import handle_lifecycle_event
from ..stream_events.assistant_boundary import is_assistant_output_boundary
from ..stream_state.segment import SegmentTracker
from engine.observability import (
    observe,
    observe_exception
)

if typing.TYPE_CHECKING:
    from ..controller import Mind


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


async def stream_looper(
    mind: "Mind",
    session: McpSessionLike,
    mode: typing.Literal["chat", "fast", "xtra"],
    pref_config: dict[str, typing.Any],
    tools: list[dict[str, typing.Any]],
    *_,
    turn_execution: TurnExecution,
    **kwargs
) -> RunResult:
    """流式模式执行器：处理流式事件、工具调用和输出上报。"""
    if mode not in {"chat", "fast", "xtra"}:
        raise ValueError(f"Invalid mode: {mode}")

    started_at = time.perf_counter()

    event_count: int = 0

    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    if not isinstance(turn_execution, TurnExecution):
        raise TypeError("turn_execution is required")

    turn_context = turn_execution.context
    hook_scope   = turn_execution.hook_scope
    message      = turn_execution.message

    if turn_context.mode != mode:
        raise ValueError("turn context mode does not match stream mode")

    kwargs["turn_id"]     = turn_context.turn_id
    kwargs["permissions"] = turn_context.permissions

    metadata = dict(turn_execution.metadata)
    kwargs["metadata"] = metadata

    if turn_execution.additional_context:
        kwargs["additional_context"] = list(
            turn_execution.additional_context
        )

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

    output_session: OutputSession = session_factory(
        mind.report.log_papers,
        animate=bool(getattr(mind, "animate", True)),
    )

    output_control: OutputControlPort = output_session.control

    status_control = output_session.status
    presentation   = output_session.presentation
    content        = output_session.content

    interrupted: bool    = False
    first_frame: bool    = True
    turn_completed: bool = False
    turn_failed: bool    = False

    turn_usage: dict[str, typing.Any] = {}

    failure_error: str | None = None
    result_status: RunStatus  = "incomplete"

    turn_hook_events: TurnHookEvents | None = None

    approvals: ApprovalStore = ApprovalStore()
    tracker: SegmentTracker  = SegmentTracker()

    idle_wait = IdleStatusTimer(
        lambda: status_control.begin_reply_wait_status(delay_sec=0.0), delay_sec=0.9
    )

    try:
        await output_control.open()

        observe(
            "stream.start",
            mode=mode,
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
            mode=mode,
            pref_config=pref_config,
            workdir=str(getattr(mind, "history_workspace", "") or ""),
            permissions=kwargs["permissions"],
            turn_id=str(kwargs.get("turn_id") or ""),
        ))

        tool_call_coordinator = ToolCallCoordinator(hook_scope)
        turn_hook_events      = TurnHookEvents(hook_scope)

        await turn_hook_events.begin(message)

        client_tool_runner = ClientToolCallRunner(
            session=session,
            output_control=output_control,
            status_control=status_control,
            presentation=presentation,
            tools=tools,
            pref_config=pref_config,
            report=mind.report,
            tool_call_coordinator=tool_call_coordinator,
        )
        plan_tool_runner = PlanToolCallRunner(
            session=session,
            output_control=output_control,
            status_control=status_control,
            presentation=presentation,
            tools=tools,
            report=mind.report,
            turn_context=turn_context,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
        )

        async for event in stream_chat(mode, pref_config, message, tools, **kwargs):
            event_count += 1
            await idle_wait.cancel()

            if ev_report:
                ev_report.bind_event(event)

            if first_frame:
                observe(
                    "stream.first_event",
                    mode=mode,
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
                tracker.commit_assistant_output()
                await content.emit(AssistantOutputBoundary())

            if event_type == "turn.start":
                continue

            if event_type == "turn.thinking":
                await status_control.begin_reply_wait_status()
                continue

            if isinstance(event, TurnFailedEvent):
                turn_failed   = True
                failure_error = event.error

                observe(
                    "stream.turn_failed",
                    level="ERROR",
                    mode=mode,
                    error=failure_error,
                )
                await finish_failure(
                    status_control,
                    presentation,
                    None,
                    phase="turn.failed",
                    error=failure_error,
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
                turn_completed = True
                turn_usage = dict(event.usage)
                break

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

                decision_source: ApprovalSource = "user"

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

                approved = decision in {"accept", "acceptForSession"}

                reason = (
                    permission_decision.reason
                    if (
                        permission_decision is not None
                        and permission_decision.action == "deny"
                    )
                    else None if approved
                    else "approval policy is never"
                    if kwargs["permissions"].approval_policy == "never"
                    else "user denied"
                )

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
                    await post_tool_approval(
                        turn_context.cid,
                        turn_context.sid,
                        event.call_id,
                        approval_id,
                        decision=decision,
                        reason=reason
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
                    await post_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        False,
                        _hook_denied_result(hook_decision.reason),
                        execution=invocation.execution,
                    )
                    await status_control.begin_reply_wait_status(delay_sec=0.15)
                    continue

                if name == PLAN_STEPS_TOOL:
                    async def execute_plan_call() -> typing.Any:
                        """执行已经获准的计划工具调用。"""
                        return await plan_tool_runner.handle(
                            invocation=invocation,
                        )

                    hook_run = await tool_call_coordinator.run(
                        invocation,
                        execute_plan_call,
                    )
                    if not hook_run.allowed:
                        plan_ok     = False
                        plan_result = _hook_denied_result(hook_run.reason)
                    else:
                        if hook_run.value is None:
                            raise RuntimeError("plan tool execution returned no result")
                        plan_ok     = hook_run.value.ok
                        plan_result = hook_run.value.fields
                    await post_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        plan_ok,
                        plan_result,
                        execution=invocation.execution,
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
                    await post_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        False,
                        approval_decision.result or {},
                        execution=invocation.execution,
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
                    await post_tool_result(
                        invocation.turn.cid,
                        invocation.turn.sid,
                        invocation.call_id,
                        invocation.name,
                        False,
                        execution_policy_result,
                        execution=invocation.execution,
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                use_coding_trace = coding_trace_tool(name)

                tool_result = await client_tool_runner.execute(
                    invocation,
                    use_coding_trace=use_coding_trace,
                )
                await post_tool_result(
                    invocation.turn.cid,
                    invocation.turn.sid,
                    invocation.call_id,
                    tool_result.name,
                    tool_result.ok,
                    tool_result.fields,
                    execution=invocation.execution,
                )
                await status_control.begin_reply_wait_status(delay_sec=0.75)
                continue

            if isinstance(event, ToolOutputEvent):
                name = event.name
                if not name:
                    continue

                arguments = dict(event.arguments)

                use_coding_trace = coding_trace_tool(name)
                tool_run         = server_tool_output_result(name, event.payload)

                if use_coding_trace:
                    await status_control.end_status()

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
        observe(
            "stream.prompt_blocked",
            level="WARNING",
            mode=mode,
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
        observe(
            "stream.interrupted",
            level="WARNING",
            mode=mode,
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise

    except Exception as e:
        result_status = "failed"
        observe_exception(
            "stream.failed",
            e,
            mode=mode,
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
        if turn_failed:
            result_status = "failed"
        elif turn_completed:
            result_status = "completed"
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
            mind.remember_last_assistant_reply(tracker.latest_assistant_output_text())

        await status_control.end_status()
        await content.emit(SourcesOutput(tuple(tracker.iter_sources())))

        if turn_completed and not turn_failed:
            await presentation.emit(build_run_completed_view(turn_usage))

        observe(
            "stream.complete",
            mode=mode,
            outcome="failed" if turn_failed else ("complete" if turn_completed else "incomplete"),
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            usage=turn_usage or None,
        )

    finally:
        if turn_hook_events is not None:
            stop_outcome = "interrupted" if interrupted else result_status
            try:
                await mind.await_cleanup(turn_hook_events.stop(
                    outcome=stop_outcome,
                    error=failure_error,
                    usage=turn_usage,
                ))
            except Exception as error:
                observe_exception(
                    "hooks.stop.failed",
                    error,
                    level="WARNING",
                    turn_id=turn_context.turn_id,
                )
        await idle_wait.cancel()
        await mind.await_cleanup(output_control.stop(blink=not interrupted))

    return RunResult(
        status=result_status,
        assistant_text=tracker.latest_assistant_output_text(),
        usage=dict(turn_usage),
        error=failure_error,
    )


if __name__ == '__main__':
    pass
