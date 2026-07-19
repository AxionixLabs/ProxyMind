# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.mcp import McpSessionLike
from mind_app.mcp.tool_store import meta_for_tool
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.approval import (
    ApprovalStore,
    approval_from_event,
    approval_id_from_event,
    validate_tool_approval
)
from mind_nova.events import EventReport
from mind_nova import request
from ..output import (
    AssistantTextDelta,
    OutputControlPort,
    SourcesOutput
)
from ..output.factory import create_output_session
from ..output.session import OutputSession
from ..presentation.approval_views import build_approval_view
from ..presentation.run_views import (
    build_run_completed_view,
    build_run_started_view,
)
from ..runtime.support.loop_support import finish_failure
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

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


async def stream_looper(
    mind: "Mind",
    session: McpSessionLike,
    mode: typing.Literal["chat", "fast", "xtra"],
    pref_config: dict[str, typing.Any],
    message: str,
    tools: list[dict[str, typing.Any]],
    *_,
    **kwargs
) -> None:
    """流式模式执行器：处理流式事件、工具调用和输出上报。"""
    if mode not in {"chat", "fast", "xtra"}:
        raise ValueError(f"Invalid mode: {mode}")

    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    if ev_report:
        request_turn_id = str(kwargs.get("turn_id") or "").strip()
        kwargs["turn_id"] = ev_report.begin_turn(request_turn_id or None)

    if not isinstance(kwargs.get("exec_env"), dict):
        service_env = (
            mind.service_exec_env_snapshot()
            if mind.is_service_mcp_linked()
            else None
        )
        kwargs["exec_env"] = build_runtime_exec_env(service_exec_env=service_env)

    session_factory = kwargs.pop("session_factory", None)

    if session_factory is None:
        session_factory = getattr(mind, "session_factory", None)
    if session_factory is None:
        session_factory = create_output_session

    output_session: OutputSession = session_factory(
        mind.report.log_papers,
        animate=bool(getattr(mind, "animate", True)),
    )

    slog: OutputControlPort = output_session.control

    presentation = output_session.presentation
    content      = output_session.content

    interrupted: bool    = False
    first_frame: bool    = True
    turn_completed: bool = False
    turn_failed: bool    = False

    turn_usage: dict[str, typing.Any] = {}

    approvals: ApprovalStore = ApprovalStore()

    idle_wait = IdleStatusTimer(
        lambda: slog.begin_reply_wait_status(delay_sec=0.0), delay_sec=0.9
    )

    try:
        await slog.open()

        tracker  = SegmentTracker()
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}

        await presentation.emit(build_run_started_view(
            metadata=metadata,
            message=message,
            mode=mode,
            pref_config=pref_config,
            workdir=str(getattr(mind, "history_workspace", "") or ""),
            sandbox=str(kwargs.get("access_mode") or ""),
            turn_id=str(kwargs.get("turn_id") or ""),
        ))

        event_ctx = StreamEventContext(
            mind=mind,
            session=session,
            slog=slog,
            presentation=presentation,
            tracker=tracker,
            mode=mode,
            pref_config=pref_config,
            metadata=metadata
        )
        tool_batch_executor = ToolBatchExecutor(
            session=session,
            stream_ui=slog,
            presentation=presentation,
            tools=tools,
            mode=mode,
            pref_config=pref_config,
            metadata=metadata,
            report=mind.report
        )
        plan_tool_runner = PlanToolCallRunner(
            session=session,
            stream_ui=slog,
            presentation=presentation,
            tools=tools,
            report=mind.report
        )

        async for event in request.stream_chat(mode, pref_config, message, tools, **kwargs):
            await idle_wait.cancel()

            if ev_report:
                ev_report.bind_event(event)

            if first_frame:
                await mind.stop_anim()
                first_frame = False

            event_type = str(event.get("type") or "")

            if is_assistant_output_boundary(event_type, event):
                tracker.commit_assistant_output()
                await slog.prepare_external_output()

            if event_type == "turn.start":
                continue

            if event_type == "turn.thinking":
                await slog.begin_reply_wait_status()
                continue

            if event_type == "turn.failed":
                turn_failed = True
                error = str(event.get("error") or "unknown error")
                await finish_failure(
                    slog,
                    presentation,
                    None,
                    phase="turn.failed",
                    error=error,
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
                await slog.settle_stream()
                slog.mark_stream_boundary()
                await slog.begin_reply_wait_status(delay_sec=0.0)
                continue

            if event_type == "text.meta":
                tracker.on_text_meta(event)
                continue

            if event_type == "turn.done":
                turn_completed = True
                turn_usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
                break

            if event_type == "tool.builtin.call":
                await slog.begin_tool_status()
                continue

            if event_type == "tool.builtin.done":
                consume_builtin_done(event, tracker)
                await slog.end_status()
                continue

            if event_type == "tool.calls.start":
                await slog.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if event_type == "tool.calls.done":
                await slog.begin_reply_wait_status(delay_sec=0.75)
                continue

            if event_type == "tool.approval_required":
                approval = approval_from_event(event)
                await slog.end_status(immediate=True)

                decision = await mind.interaction.request_approval(approval)

                if decision == "expired":
                    await presentation.emit(build_approval_view(
                        approval,
                        decision=decision,
                    ))
                    await slog.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                    continue

                approved    = decision in {"accept", "acceptForSession"}
                approval_id = approval_id_from_event(event)
                reason      = None if approved else "user denied"

                approvals.mark_decision(
                    call_id=str(event.get("call_id") or ""), approval=approval, decision=decision
                )

                await presentation.emit(build_approval_view(
                    approval,
                    decision=decision,
                ))
                try:
                    await request.post_tool_approval(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        approval_id,
                        decision=decision,
                        reason=reason
                    )
                except request.ToolApprovalExpired:
                    pass
                if not approved:
                    await slog.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if event_type == "tool.call":
                name      = str(event.get("name") or event.get("tool") or "").strip()
                arguments = event.get("arguments", {})

                if not name:
                    await request.post_tool_result(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        "",
                        False,
                        {"error": "tool.call missing name/tool"},
                        execution=event.get("execution") if isinstance(event.get("execution"), dict) else None
                    )
                    await slog.begin_reply_wait_status()
                    continue

                if not isinstance(arguments, dict):
                    arguments = {}

                if name == PLAN_STEPS_TOOL:
                    await plan_tool_runner.handle(
                        event=event,
                        arguments=arguments
                    )
                    await slog.begin_reply_wait_status(delay_sec=0.75)
                    continue

                event_meta      = event.get("meta") if isinstance(event.get("meta"), dict) else None
                event_execution = event.get("execution") if isinstance(event.get("execution"), dict) else None

                approval_decision = validate_tool_approval(
                    event=event,
                    name=name,
                    arguments=arguments,
                    store=approvals,
                    meta=event_meta,
                    local_meta=meta_for_tool(tools, name)
                )

                if approval_decision.action == "wait":
                    await slog.begin_reply_wait_status()
                    continue

                if approval_decision.action == "reject":
                    await request.post_tool_result(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        name,
                        False,
                        approval_decision.result or {},
                        execution=event_execution
                    )
                    await slog.begin_reply_wait_status()
                    continue

                if execution_policy_result := validate_execution_policy(
                    name=name,
                    execution=event_execution
                ):
                    if is_execution_ignored(execution_policy_result):
                        await slog.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                        continue
                    await request.post_tool_result(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        name,
                        False,
                        execution_policy_result,
                        execution=event_execution
                    )
                    await slog.begin_reply_wait_status()
                    continue

                use_coding_trace = coding_trace_tool(name)
                local_tool_meta  = meta_for_tool(tools, name)
                effective_meta   = {**(local_tool_meta or {}),**(event_meta or {})} or None

                pending_call = PendingToolCall(
                    event=event,
                    name=name,
                    arguments=arguments,
                    meta=effective_meta,
                    execution=event_execution,
                    use_coding_trace=use_coding_trace
                )

                await tool_batch_executor.execute_call(pending_call)
                await slog.begin_reply_wait_status(delay_sec=0.75)
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
                    await slog.end_status()

                await show_tool_result(
                    presentation,
                    name,
                    arguments,
                    tool_run,
                    use_coding_trace=use_coding_trace,
                    call_id=str(event.get("call_id") or ""),
                )

                await slog.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if await handle_lifecycle_event(event_type, event, event_ctx):
                continue

            continue

    except asyncio.CancelledError:
        interrupted = True
        raise

    except Exception as e:
        error = friendly_exception_text(e)
        await mind.await_cleanup(mind.stop_anim())
        await finish_failure(
            slog,
            presentation,
            ev_report,
            phase="turn.failed",
            error=error
        )

    else:
        if turn_completed:
            mind.remember_last_assistant_reply(tracker.latest_assistant_output_text())

        await slog.end_status()
        await content.emit(SourcesOutput(tuple(tracker.iter_sources())))

        if turn_completed and not turn_failed:
            await presentation.emit(build_run_completed_view(turn_usage))

    finally:
        await idle_wait.cancel()
        await mind.await_cleanup(slog.stop(blink=not interrupted))


if __name__ == '__main__':
    pass
