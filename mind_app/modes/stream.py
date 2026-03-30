# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mcp import ClientSession
from engine.enhancer import Enhancer
from engine.tinker import Tooling
from mind_nova.events import EventReport
from mind_nova import request
from .tool_result import tool_result_text
from ..stream_ui import StreamUI
from ..stream_events.finish import finish_stream
from ..stream_events.responses_builtin import (
    resolve_builtin_name,
    consume_builtin_done
)
from ..stream_state.segment import (
    SegmentTracker,
    build_sources_text
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

    exclude = [{"domain": "common", "class": "inspect", "name": "free_rule"}]
    if mode == "fast":
        exclude = [
            {"domain": "device"},
            {"domain": "bench", "class": "framix"},
            {"domain": "bench", "class": "memrix"},
            {"domain": "common", "class": "inspect"},
            {"domain": "media", "class": "screen"}
        ]

    filtered_tools = Tooling.filter_tools(openai_tools, tool_meta, exclude=exclude)
    ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

    slog: StreamUI = StreamUI(mind.report.log_papers)
    interrupted = False

    try:
        await slog.open()
        first_frame = True
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
                await slog.feed(chunk=error, display=StreamUI.BLOCK)
                return None

            if event_type == "text.delta":
                text = str(event.get("text") or "")
                tracker.on_text_delta(event)
                await slog.feed(chunk=text, display=StreamUI.STREAM)
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
                summary = Tooling.summarize_tool_arguments(name, arguments)

                if Tooling.needs_wakeup(tool_meta, name):
                    if error := await mind.wakeup(session, slog):
                        await slog.feed(error, display=StreamUI.BLOCK)
                        await finish_stream(ev_report, phase="turn.failed", error=str(error))
                        return None

                await slog.feed(
                    chunk=f"{name} {arguments}",
                    display=StreamUI.BLOCK,
                    display_chunk=summary
                )

                arguments = Enhancer.exchange(name, arguments, mind.report)
                await slog.begin_tool_status()
                try:
                    result = await session.call_tool(name, arguments)
                    ok = not result.isError

                    enhancer: Enhancer = Enhancer(session, mode, model_api, kwargs.get("metadata"))
                    fields = await enhancer.enhance(name, arguments, result, ok, slog)
                finally:
                    await slog.end_status()

                await slog.feed(chunk=tool_result_text(fields), display=StreamUI.BLOCK)

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
        await slog.feed(chunk=str(e), display=StreamUI.BLOCK)
        await finish_stream(ev_report, phase="turn.failed", error=f"{type(e).__name__}: {e}")
        return None

    else:
        await slog.end_status()
        await slog.feed(chunk=build_sources_text(tracker), display=StreamUI.BLOCK)

    finally:
        await mind.await_cleanup(slog.stop(blink=not interrupted))


if __name__ == '__main__':
    pass
