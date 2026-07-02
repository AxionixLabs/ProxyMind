# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.mcp import McpSessionLike
from mind_app.mcp.tool_store import meta_for_tool
from mind_app.approval import (
    ApprovalStore,
    approval_from_event,
    approval_id_from_event,
    prompt_tool_approval_decision,
    validate_tool_approval
)
from mind_nova.events import EventReport
from mind_nova import request
from ..stream_ui import StreamUI
from ..runtime.loop_support import finish_failure
from ..runtime.session_policy import friendly_exception_text
from ..runtime.tool_run import (
    server_tool_output_result
)
from ..runtime.tool_display import (
    show_tool_result,
)
from ..runtime.execution_policy import (
    is_execution_ignored,
    validate_execution_policy
)
from ..runtime.tool_batch import (
    PendingToolCall,
    ToolBatchExecutor,
    ToolCallBatch,
    batch_from_event
)
from ..runtime.idle_status import IdleStatusTimer
from ..stream_events.responses_builtin import (
    resolve_builtin_name,
    consume_builtin_done
)
from ..stream_events.approval_trace import (
    render_approval_approved_trace,
    render_approval_denied_trace,
    render_approval_expired_trace,
    render_approval_trace_parts
)
from ..stream_events.tool_trace import coding_trace_tool
from ..stream_events.lifecycle import (
    StreamEventContext,
    handle_lifecycle_event
)
from ..stream_state.segment import (
    SegmentTracker, build_sources_text
)

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
    """流式模式执行器：处理 chat/fast/xtra 的事件流、工具调用和输出上报。"""
    if mode not in {"chat", "fast", "xtra"}:
        raise ValueError(f"Invalid mode: {mode}")

    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)
    approval_input_func = kwargs.pop("approval_input_func", None)

    slog: StreamUI = StreamUI(mind.report.log_papers, design_level=mind.level)

    interrupted: bool = False
    first_frame: bool = True

    approvals = ApprovalStore()

    idle_wait = IdleStatusTimer(
        lambda: slog.begin_reply_wait_status(delay_sec=0.0),
        delay_sec=0.9
    )

    try:
        await slog.open()

        tracker  = SegmentTracker()
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}

        event_ctx = StreamEventContext(
            mind=mind,
            session=session,
            slog=slog,
            tracker=tracker,
            mode=mode,
            pref_config=pref_config,
            metadata=metadata
        )
        current_batch: ToolCallBatch | None = None
        tool_batch_executor = ToolBatchExecutor(
            session=session,
            stream_ui=slog,
            tools=tools,
            mode=mode,
            pref_config=pref_config,
            metadata=metadata,
            report=mind.report
        )

        async for event in request.stream_chat(
            mode,
            pref_config,
            message,
            tools,
            **kwargs
        ):
            await idle_wait.cancel()

            if ev_report:
                ev_report.bind_event(event)

            if first_frame:
                await mind.stop_anim()
                first_frame = False

            event_type = str(event.get("type") or "")

            if event_type == "turn.start":
                continue

            if event_type == "turn.thinking":
                await slog.begin_reply_wait_status()
                continue

            if event_type == "turn.failed":
                error = str(event.get("error") or "unknown error")
                await finish_failure(slog, None, phase="turn.failed", error=error)
                continue

            if event_type == "text.delta":
                text = str(event.get("text") or "")
                tracker.on_text_delta(event)
                await slog.feed(text, display=StreamUI.STREAM)
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
                if current_batch is not None:
                    await tool_batch_executor.execute_batch(current_batch)
                break

            if event_type == "tool.builtin.call":
                builtin_name = resolve_builtin_name(event)
                await slog.begin_builtin_status(builtin_name)
                continue

            if event_type == "tool.builtin.done":
                consume_builtin_done(event, tracker)
                await slog.end_status()
                continue

            if event_type == "tool.calls.start":
                current_batch = batch_from_event(event)
                await slog.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                continue

            if event_type == "tool.calls.done":
                if current_batch is not None:
                    await tool_batch_executor.execute_batch(current_batch)
                    current_batch = None
                await slog.begin_reply_wait_status(delay_sec=0.75)
                continue

            if event_type == "tool.approval_required":
                approval = approval_from_event(event)
                await slog.end_status(immediate=True)
                await slog.prepare_external_output()

                decision = await prompt_tool_approval_decision(
                    approval,
                    input_func=approval_input_func
                )

                if decision == "expired":
                    expired_title = render_approval_expired_trace(approval)
                    expired_parts = [
                        *render_approval_trace_parts(
                            expired_title, approval=approval, state="denied"
                        )
                    ]
                    await slog.print_block(
                        expired_title, display_parts=expired_parts
                    )
                    await slog.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                    continue

                approved    = decision in {"accept", "acceptForSession"}
                approval_id = approval_id_from_event(event)
                reason      = None if approved else "user denied"

                approvals.mark_decision(
                    call_id=str(event.get("call_id") or ""), approval=approval, decision=decision
                )

                done_title = (
                    render_approval_approved_trace(approval, decision=decision)
                    if approved else render_approval_denied_trace(approval)
                )
                done_parts = [
                    *render_approval_trace_parts(
                        done_title, approval=approval, state="approved" if approved else "denied"
                    )
                ]
                await slog.print_block(
                    done_title, display_parts=done_parts
                )
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
                name = str(event.get("name") or event.get("tool") or "").strip()
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

                event_meta = event.get("meta") if isinstance(event.get("meta"), dict) else None
                event_execution = event.get("execution") if isinstance(event.get("execution"), dict) else None
                if not isinstance(arguments, dict):
                    arguments = {}

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
                    arguments=arguments,
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

                local_tool_meta = meta_for_tool(tools, name)
                effective_meta = {
                    **(local_tool_meta or {}),
                    **(event_meta or {})
                } or None
                pending_call = PendingToolCall(
                    event=event,
                    name=name,
                    arguments=arguments,
                    meta=effective_meta,
                    execution=event_execution,
                    use_coding_trace=use_coding_trace
                )

                call_id = str(event.get("call_id") or "")
                if current_batch is not None and current_batch.contains(call_id):
                    current_batch.calls.append(pending_call)
                    await slog.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)
                    continue

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

                await show_tool_result(
                    slog,
                    name,
                    arguments,
                    tool_run,
                    use_coding_trace=use_coding_trace
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
        await finish_failure(slog, ev_report, phase="turn.failed", error=error)

    else:
        await slog.end_status()
        await slog.feed(build_sources_text(tracker), display=StreamUI.BLOCK)

    finally:
        await idle_wait.cancel()
        await mind.await_cleanup(slog.stop(blink=not interrupted))


if __name__ == '__main__':
    pass
