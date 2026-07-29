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
from mind_nova.requests.tools import (
    ToolApprovalExpired,
    post_tool_approval,
    post_tool_result
)
from ..output import (
    AssistantTextDelta,
    OutputControlPort,
    SourcesOutput
)
from ..output.rich import create_rich_output_session
from ..output.session import OutputSession
from .result import (
    RunResult,
    RunStatus
)
from ..presentation.approval_views import build_approval_view
from ..presentation.run_views import (
    build_run_completed_view,
    build_run_started_view
)
from ..runtime.support.loop_support import finish_failure
from ..runtime.execution import (
    ToolInvocation,
    TurnContext
)
from ..runtime.hooks.runtime import HookRuntime
from ..runtime.hooks.tool import ToolCallCoordinator
from ..runtime.environment.exec_env import build_runtime_exec_env
from ..runtime.support.session_policy import friendly_exception_text
from ..runtime.tools.run import server_tool_output_result
from ..runtime.tools.display import show_tool_result
from ..runtime.tools.execution_policy import (
    is_execution_ignored,
    validate_execution_policy
)
from ..runtime.tools.batch import (
    PendingToolCall,
    ToolBatchExecutor
)
from ..runtime.tools.plan_call import PlanToolCallRunner
from ..runtime.support.idle_status import IdleStatusTimer
from ..stream_events.responses_builtin import consume_builtin_done
from ..stream_events.tool_trace import coding_trace_tool
from ..stream_events.lifecycle import (
    StreamEventContext,
    handle_lifecycle_event
)
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
    event: dict[str, typing.Any],
    tools: list[dict[str, typing.Any]],
    *,
    arguments: dict[str, typing.Any]
) -> ToolInvocation:
    """从流式事件构建不暴露内部授权字段的工具调用上下文。"""
    name           = str(event.get("name") or event.get("tool") or "").strip()
    event_meta     = event.get("meta") if isinstance(event.get("meta"), dict) else None
    local_meta     = meta_for_tool(tools, name)
    effective_meta = {**(local_meta or {}), **(event_meta or {})} or None
    execution      = event.get("execution") if isinstance(event.get("execution"), dict) else None

    return ToolInvocation(
        turn=turn_context,
        call_id=str(event.get("call_id") or ""),
        name=name,
        arguments=arguments,
        meta=effective_meta,
        execution=execution,
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
    message: str,
    tools: list[dict[str, typing.Any]],
    *_,
    **kwargs
) -> RunResult:
    """流式模式执行器：处理流式事件、工具调用和输出上报。"""
    if mode not in {"chat", "fast", "xtra"}:
        raise ValueError(f"Invalid mode: {mode}")

    started_at = time.perf_counter()

    event_count: int = 0

    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    turn_context = kwargs.pop("turn_context", None)

    if not isinstance(turn_context, TurnContext):
        raise TypeError("turn_context is required")
    if turn_context.mode != mode:
        raise ValueError("turn context mode does not match stream mode")

    kwargs["turn_id"]     = turn_context.turn_id
    kwargs["permissions"] = turn_context.permissions

    metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}

    metadata = {
        **metadata,
        "cid": turn_context.cid,
        "sid": turn_context.sid,
    }
    kwargs["metadata"] = metadata

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
    if request_skills is None or (
        isinstance(request_skills, (list, tuple)) and not request_skills
    ):
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
        session_factory = create_rich_output_session

    output_session: OutputSession = session_factory(
        mind.report.log_papers,
        animate=bool(getattr(mind, "animate", True)),
    )

    output_control: OutputControlPort = output_session.control
    status_control = output_session.status

    presentation = output_session.presentation
    content      = output_session.content

    interrupted: bool    = False
    first_frame: bool    = True
    turn_completed: bool = False
    turn_failed: bool    = False

    turn_usage: dict[str, typing.Any] = {}
    failure_error: str | None = None
    result_status: RunStatus = "incomplete"

    approvals: ApprovalStore = ApprovalStore()
    tracker = SegmentTracker()

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

        event_ctx = StreamEventContext(
            mind=mind,
            session=session,
            output_control=output_control,
            status_control=status_control,
            presentation=presentation,
            tracker=tracker,
            mode=mode,
            pref_config=pref_config,
            metadata=metadata
        )

        hook_runtime = getattr(mind, "hooks", None)
        if not isinstance(hook_runtime, HookRuntime):
            hook_runtime = HookRuntime.empty()

        tool_call_coordinator = ToolCallCoordinator(hook_runtime)

        tool_batch_executor = ToolBatchExecutor(
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
                    event_type=event.get("type"),
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                )
                if not mind.frontend.runtime.active:
                    await mind.stop_anim("wait")
                first_frame = False

            event_type = str(event.get("type") or "")

            if is_assistant_output_boundary(event_type, event):
                tracker.commit_assistant_output()
                await output_control.prepare_external_output()

            if event_type == "turn.start":
                continue

            if event_type == "turn.thinking":
                await status_control.begin_reply_wait_status()
                continue

            if event_type == "turn.failed":
                turn_failed = True
                failure_error = str(event.get("error") or "unknown error")
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

            if event_type == "text.delta":
                text = str(event.get("text") or "")
                tracker.on_text_delta(event)
                await content.emit(AssistantTextDelta(text))
                idle_wait.reschedule()
                continue

            if event_type == "text.done":
                tracker.on_text_done(event)
                await output_control.settle_stream()
                output_control.mark_stream_boundary()
                await status_control.begin_reply_wait_status()
                continue

            if event_type == "text.meta":
                tracker.on_text_meta(event)
                continue

            if event_type == "turn.done":
                turn_completed = True
                turn_usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                break

            if event_type == "tool.builtin.call":
                await status_control.begin_tool_status()
                continue

            if event_type == "tool.builtin.done":
                consume_builtin_done(event, tracker)
                await status_control.end_status()
                continue

            if event_type == "tool.calls.start":
                await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if event_type == "tool.calls.done":
                await status_control.begin_reply_wait_status(delay_sec=0.75)
                continue

            if event_type == "tool.approval_required":
                approval = approval_from_event(event)
                await status_control.end_status(immediate=True)

                approval_started_at = time.perf_counter()

                approval_id      = approval_id_from_event(event)
                approval_tool    = str(event.get("name") or event.get("tool") or "")
                approval_call_id = str(event.get("call_id") or "")

                hook_decision = None

                if approval_tool:
                    approval_arguments = event.get("arguments")
                    if not isinstance(approval_arguments, dict):
                        raw_approval_arguments = approval.get("arguments")
                        approval_arguments = (
                            dict(raw_approval_arguments)
                            if isinstance(raw_approval_arguments, dict)
                            else {}
                        )
                    hook_decision = await tool_call_coordinator.prepare(
                        _tool_invocation_from_event(
                            turn_context,
                            event,
                            tools,
                            arguments=approval_arguments,
                        )
                    )

                observe(
                    "approval.requested",
                    tool=approval_tool,
                    call_id=approval_call_id,
                    approval_id=approval_id,
                )

                if hook_decision is not None and not hook_decision.allowed:
                    decision = "decline"
                elif kwargs["permissions"].approval_policy == "never":
                    decision = "decline"
                else:
                    decision = await mind.frontend.interaction.request_approval(approval)

                observe(
                    "approval.decided",
                    tool=approval_tool,
                    call_id=approval_call_id,
                    approval_id=approval_id,
                    decision=decision,
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
                    ))
                    await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                    continue

                approved = decision in {"accept", "acceptForSession"}

                reason = (
                    hook_decision.reason
                    if hook_decision is not None and not hook_decision.allowed
                    else None if approved
                    else "approval policy is never"
                    if kwargs["permissions"].approval_policy == "never"
                    else "user denied"
                )

                approvals.mark_decision(
                    call_id=str(event.get("call_id") or ""), approval=approval, decision=decision
                )

                await presentation.emit(build_approval_view(
                    approval,
                    decision=decision,
                ))
                try:
                    await post_tool_approval(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
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

            if event_type == "tool.call":
                name      = str(event.get("name") or event.get("tool") or "").strip()
                arguments = event.get("arguments", {})

                if not name:
                    await post_tool_result(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        "",
                        False,
                        {"error": "tool.call missing name/tool"},
                        execution=event.get("execution") if isinstance(event.get("execution"), dict) else None
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                if not isinstance(arguments, dict):
                    arguments = {}

                invocation = _tool_invocation_from_event(
                    turn_context,
                    event,
                    tools,
                    arguments=arguments,
                )

                hook_decision = await tool_call_coordinator.prepare(invocation)
                if not hook_decision.allowed:
                    await post_tool_result(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        name,
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
                            event=event,
                            arguments=arguments,
                        )

                    hook_run = await tool_call_coordinator.run(
                        invocation,
                        execute_plan_call,
                    )
                    if not hook_run.allowed:
                        await post_tool_result(
                            event["cid"],
                            event["sid"],
                            event["call_id"],
                            name,
                            False,
                            _hook_denied_result(hook_run.reason),
                            execution=invocation.execution,
                        )
                    await status_control.begin_reply_wait_status(delay_sec=0.75)
                    continue

                event_meta      = event.get("meta") if isinstance(event.get("meta"), dict) else None
                event_execution = event.get("execution") if isinstance(event.get("execution"), dict) else None

                approval_decision = validate_tool_approval(
                    event=event,
                    name=name,
                    arguments=arguments,
                    store=approvals,
                    meta=event_meta,
                    local_meta=meta_for_tool(tools, name),
                    approval_policy=kwargs["permissions"].approval_policy,
                )

                if approval_decision.action == "wait":
                    observe(
                        "approval.enforced",
                        action="wait",
                        tool=name,
                        call_id=event.get("call_id"),
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                if approval_decision.action == "reject":
                    observe(
                        "approval.enforced",
                        level="WARNING",
                        action="reject",
                        tool=name,
                        call_id=event.get("call_id"),
                    )
                    await post_tool_result(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        name,
                        False,
                        approval_decision.result or {},
                        execution=event_execution
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                if execution_policy_result := validate_execution_policy(
                    name=name,
                    execution=event_execution
                ):
                    if is_execution_ignored(execution_policy_result):
                        await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                        continue
                    await post_tool_result(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        name,
                        False,
                        execution_policy_result,
                        execution=event_execution
                    )
                    await status_control.begin_reply_wait_status()
                    continue

                use_coding_trace = coding_trace_tool(name)

                pending_call = PendingToolCall(
                    event=event,
                    invocation=invocation,
                    use_coding_trace=use_coding_trace
                )

                await tool_batch_executor.execute_call(pending_call)
                await status_control.begin_reply_wait_status(delay_sec=0.75)
                continue

            if event_type == "tool.output":
                name = str(event.get("name") or event.get("tool") or "").strip()
                if not name:
                    continue

                arguments = event.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}

                use_coding_trace = coding_trace_tool(name)
                tool_run         = server_tool_output_result(name, event)

                if use_coding_trace:
                    await status_control.end_status()

                await show_tool_result(
                    presentation,
                    name,
                    arguments,
                    tool_run,
                    use_coding_trace=use_coding_trace,
                    call_id=str(event.get("call_id") or ""),
                )

                await status_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if await handle_lifecycle_event(event_type, event, event_ctx):
                continue

            continue

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

        if turn_completed:
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
