# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.mcp import McpSessionLike
from engine.enhancer import Enhancer
from mind_nova.events import EventReport
from mind_nova import request
from ..stream_ui import StreamUI
from ..runtime.loop_support import (
    ensure_wakeup, finish_failure
)
from ..runtime.tool_run import (
    run_tool_step,
    server_tool_output_result
)
from ..runtime.tool_display import (
    show_tool_result,
    show_tool_start
)
from mind_app.approval import (
    ApprovalStore,
    approval_from_event,
    approval_id_from_event,
    prompt_tool_approval_decision,
    validate_tool_approval
)
from ..runtime.execution_policy import (
    is_execution_ignored,
    should_pass_execution_to_tool,
    validate_execution_policy
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
from ..stream_events.tool_trace import is_native_coding_trace_tool
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
    openai_tools: list[dict[str, typing.Any]],
    tool_meta: dict[str, dict[str, typing.Any]],
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

        async for event in request.stream_chat(
            mode,
            pref_config,
            message,
            openai_tools,
            tool_meta=tool_meta,
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
                break

            if event_type == "tool.builtin.call":
                builtin_name = resolve_builtin_name(event)
                await slog.begin_builtin_status(builtin_name)
                continue

            if event_type == "tool.builtin.done":
                consume_builtin_done(event, tracker)
                await slog.end_status()
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
                    tool_meta=tool_meta.get(name) if isinstance(tool_meta, dict) else None
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

                if error := await ensure_wakeup(
                    mind,
                    session,
                    slog,
                    tool_meta=tool_meta,
                    name=name,
                    meta=event_meta
                ):
                    await finish_failure(slog, ev_report, phase="turn.failed", error=error)
                    continue

                use_coding_trace = is_native_coding_trace_tool(name)

                if not use_coding_trace:
                    await show_tool_start(
                        slog,
                        name,
                        arguments,
                        call_id=str(event.get("call_id") or "")
                    )
                else:
                    slog.record_tool_arguments(
                        name,
                        arguments,
                        call_id=str(event.get("call_id") or "")
                    )

                arguments = Enhancer.exchange(name, arguments, mind.report)
                if should_pass_execution_to_tool(name, event_execution):
                    arguments = {**arguments, "execution": event_execution}

                tool_run = await run_tool_step(
                    session,
                    stream_ui=slog,
                    tool_meta=tool_meta,
                    name=name,
                    arguments=arguments,
                    meta=event_meta,
                    mode=mode,
                    pref_config=pref_config,
                    metadata=kwargs.get("metadata") or {},
                    enable_progress_notify=True,
                    stream_callback=lambda x: slog.feed(
                        x, display=StreamUI.BLOCK
                    ),
                    status_text="coding" if use_coding_trace else None,
                    code_status=use_coding_trace
                )

                ok     = tool_run.ok
                fields = tool_run.fields
                text   = tool_run.text

                await show_tool_result(
                    slog,
                    name,
                    arguments,
                    tool_run,
                    ok=ok,
                    fields=fields,
                    text=text,
                    use_coding_trace=use_coding_trace
                )

                await request.post_tool_result(
                    event["cid"],
                    event["sid"],
                    event["call_id"],
                    name,
                    ok,
                    fields,
                    execution=event_execution
                )
                await slog.begin_reply_wait_status(delay_sec=0.75)
                continue

            if event_type == "tool.output":
                name = str(event.get("name") or event.get("tool") or "").strip()
                if not name:
                    continue

                arguments = event.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}

                use_coding_trace = is_native_coding_trace_tool(name)
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
        error = f"{type(e).__name__}: {e}"
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
