# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.mcp import McpSessionLike
from engine.enhancer import Enhancer
from engine.tinker import Tooling
from mind_nova.events import EventReport
from mind_nova import request
from ..stream_ui import StreamUI
from ..runtime.loop_support import (
    ensure_wakeup, finish_failure
)
from ..runtime.tool_run import run_tool_step
from ..runtime.cloud_sandbox import normalize_cloud_sandbox_handoff
from ..runtime.tool_approval import (
    ApprovalStore,
    approval_prompt_parts,
    approval_prompt_text,
    approval_id_from_event,
    prompt_tool_approval_decision,
    validate_shell_approval
)
from ..stream_events.responses_builtin import (
    resolve_builtin_name,
    consume_builtin_done
)
from ..stream_events.approval_trace import (
    render_approval_approved_trace,
    render_approval_denied_trace,
    render_approval_trace_parts
)
from ..stream_events.tool_trace import (
    MISSING,
    is_native_coding_trace_tool,
    local_path_exists,
    render_tool_result_preview,
    render_tool_start_trace,
    render_tool_trace,
    render_tool_trace_parts
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
    model_api: dict[str, typing.Any],
    message: str,
    openai_tools: list[dict[str, typing.Any]],
    tool_meta: dict[str, dict[str, typing.Any]],
    *_,
    **kwargs
) -> None:
    """流式模式执行器：处理 chat/fast/xtra 的事件流、工具调用和输出上报。"""

    common_exclude = [
        {"domain": "common", "class": "inspect", "name": "free_rule"}
    ]

    if mode == "chat":
        exclude = [
            *common_exclude,
            {"domain": "common", "class": "security"},
            {"domain": "bench", "class": "k6"},
            {"domain": "bench", "class": "nexus"},
            {"domain": "media", "class": "ffmpeg"}
        ]
        filtered_tools = Tooling.filter_tools(openai_tools, tool_meta, exclude=exclude)
    elif mode == "fast":
        exclude = [
            *common_exclude,
            {"domain": "device"},
            {"domain": "bench", "class": "framix"},
            {"domain": "bench", "class": "memrix"},
            {"domain": "media", "class": "screen"}
        ]
        filtered_tools = Tooling.filter_tools(openai_tools, tool_meta, exclude=exclude)
    elif mode == "xtra":
        filtered_tools = Tooling.filter_xtra_mode_tools(openai_tools, tool_meta)
    else:
        raise ValueError(f"Invalid mode: {mode}")

    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)
    approval_input_func = kwargs.pop("approval_input_func", None)

    slog: StreamUI = StreamUI(mind.report.log_papers)

    interrupted = False
    first_frame = True
    approvals = ApprovalStore()

    try:
        await slog.open()
        tracker = SegmentTracker()

        async for event in request.stream_chat(mode, model_api, message, filtered_tools, **kwargs):
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
                continue

            if event_type == "text.done":
                tracker.on_text_done(event)
                await slog.settle_stream()
                await slog.begin_reply_wait_status(delay_sec=0.45)
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
                approval = event.get("approval") if isinstance(event.get("approval"), dict) else {}
                await slog.end_status()
                await slog.settle_stream()
                await slog.feed(
                    approval_prompt_text(approval),
                    display=StreamUI.BLOCK,
                    display_parts=approval_prompt_parts(approval)
                )
                await slog.settle_stream()
                await slog.commit_live()

                decision = await prompt_tool_approval_decision(
                    approval,
                    input_func=approval_input_func,
                    show_prompt=False
                )
                approved = decision in {"accept", "acceptForSession"}
                approval_id = approval_id_from_event(event)
                reason = None if approved else "user denied"
                approvals.mark_decision(
                    call_id=str(event.get("call_id") or ""),
                    approval=approval,
                    decision=decision
                )

                done_title = (
                    render_approval_approved_trace(approval)
                    if approved else render_approval_denied_trace(approval)
                )
                await slog.feed(
                    f"{done_title}\n",
                    display=StreamUI.BLOCK,
                    display_parts=render_approval_trace_parts(
                        done_title,
                        approval=approval,
                        state="approved" if approved else "denied"
                    )
                )
                await slog.begin_work_status("Running")
                await request.post_tool_approval(
                    event["cid"],
                    event["sid"],
                    event["call_id"],
                    approval_id,
                    decision=decision,
                    reason=reason
                )
                continue

            if event_type == "tool.call":
                name, arguments = event["name"], event.get("arguments", {})
                event_meta = event.get("meta") if isinstance(event.get("meta"), dict) else None
                summary = Tooling.summarize_tool_arguments(name, arguments)
                if not isinstance(arguments, dict):
                    arguments = {}

                approval_decision = validate_shell_approval(
                    event=event,
                    name=name,
                    arguments=arguments,
                    store=approvals
                )
                if approval_decision.action == "reject":
                    await request.post_tool_result(
                        event["cid"],
                        event["sid"],
                        event["call_id"],
                        name,
                        False,
                        approval_decision.result or {}
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

                before_exists = (
                    local_path_exists(arguments)
                    if name in {"workspace_write_file", "workspace_apply_patch"}
                    else MISSING
                )
                use_coding_trace = is_native_coding_trace_tool(name)
                trace_start = render_tool_start_trace(
                    name,
                    arguments,
                    before_exists=before_exists
                )
                if not use_coding_trace:
                    await slog.feed(
                        f"{trace_start}\n",
                        display=StreamUI.BLOCK,
                        display_chunk=summary
                    )

                arguments = Enhancer.exchange(name, arguments, mind.report)
                tool_run = await run_tool_step(
                    session,
                    stream_ui=slog,
                    tool_meta=tool_meta,
                    name=name,
                    arguments=arguments,
                    meta=event_meta,
                    mode=mode,
                    model_api=model_api,
                    metadata=kwargs.get("metadata") or {},
                    enable_progress_notify=True,
                    stream_callback=lambda x: slog.feed(
                        f"{x}\n", display=StreamUI.BLOCK
                    ),
                    status_text="coding workspace" if use_coding_trace else None,
                    code_status=use_coding_trace
                )

                ok = tool_run.ok
                fields = tool_run.fields
                text = tool_run.text
                if handoff := normalize_cloud_sandbox_handoff(
                    tool_name=name,
                    fields=fields,
                    ok=ok
                ):
                    ok = True
                    fields = handoff
                    text = str(handoff.get("text") or "")

                if use_coding_trace:
                    await slog.end_status()
                    trace_title = render_tool_trace(
                        name,
                        arguments,
                        ok=ok,
                        data=tool_run.data,
                        cost_ms=tool_run.cost_ms,
                        before_exists=before_exists
                    )
                    trace_preview = render_tool_result_preview(name, tool_run.data)
                    trace_parts = render_tool_trace_parts(
                        trace_title,
                        preview=trace_preview,
                        ok=ok
                    )
                    trace_text = trace_title
                    if trace_preview.full:
                        indented_preview = trace_preview.full.replace("\n", "\n  ")
                        trace_text = f"{trace_title}\n└ {indented_preview}"
                    await slog.feed(
                        f"{trace_text}\n",
                        display=StreamUI.BLOCK,
                        display_parts=trace_parts
                    )
                else:
                    await slog.feed(text, display=StreamUI.BLOCK)

                await request.post_tool_result(
                    event["cid"], event["sid"], event["call_id"], name, ok, fields
                )
                await slog.begin_reply_wait_status()
                continue

            if event_type == "tool.output":
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
        await slog.feed(f"{build_sources_text(tracker)}\n", display=StreamUI.BLOCK)

    finally:
        await mind.await_cleanup(slog.stop(blink=not interrupted))


if __name__ == '__main__':
    pass
