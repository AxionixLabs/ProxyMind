# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mcp import ClientSession
from engine.enhancer import Enhancer
from engine.tinker import Tooling
from mind_nova.events import EventReport
from mind_nova import request
from ..stream_ui import StreamUI
from ..runtime.loop_support import (
    ensure_wakeup, finish_failure
)
from ..runtime.tool_run import run_tool_step
from ..stream_events.responses_builtin import (
    resolve_builtin_name, consume_builtin_done
)
from ..stream_state.segment import (
    SegmentTracker, build_sources_text
)

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


async def stream_looper(
    mind: "Mind",
    session: ClientSession,
    mode: typing.Literal["chat", "fast"],
    model_api: dict[str, typing.Any],
    message: str,
    openai_tools: list[dict[str, typing.Any]],
    tool_meta: dict[str, dict[str, typing.Any]],
    *_,
    **kwargs
) -> None:
    """流式模式执行器：处理 chat/fast 的事件流、工具调用和输出上报。"""

    exclude = [
        {"domain": "common", "class": "inspect", "name": "free_rule"}
    ]

    if mode == "chat":
        exclude = [
            *exclude,
            {"domain": "common", "class": "security"},
            {"domain": "bench", "class": "k6"},
            {"domain": "bench", "class": "nexus"},
            {"domain": "media", "class": "ffmpeg"}
        ]
    elif mode == "fast":
        exclude = [
            *exclude,
            {"domain": "device"},
            {"domain": "bench", "class": "framix"},
            {"domain": "bench", "class": "memrix"},
            {"domain": "media", "class": "screen"}
        ]
    else:
        raise ValueError(f"Invalid mode: {mode}")

    filtered_tools = Tooling.filter_tools(openai_tools, tool_meta, exclude=exclude)
    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    slog: StreamUI = StreamUI(mind.report.log_papers)

    interrupted = False
    first_frame = True

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
                await finish_failure(slog, ev_report, phase="turn.failed", error=error)
                continue

            if event_type == "text.delta":
                text = str(event.get("text") or "")
                tracker.on_text_delta(event)
                await slog.feed(text, display=StreamUI.STREAM)
                continue

            if event_type == "text.done":
                tracker.on_text_done(event)
                await slog.settle_stream()
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

            if event_type == "tool.call":
                name, arguments = event["name"], event.get("arguments", {})
                event_meta = event.get("meta") if isinstance(event.get("meta"), dict) else None
                summary = Tooling.summarize_tool_arguments(name, arguments)

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

                await slog.feed(
                    f"{name} {arguments}",
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
                    )
                )

                ok = tool_run.ok
                fields = tool_run.fields
                await slog.feed(f"{tool_run.text}\n", display=StreamUI.BLOCK)

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
