# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from mcp import ClientSession
from engine.enhancer import Enhancer
from engine.tinker import (
    Tooling, StreamTyperLogger
)
from mind_nova.request import EventReport
from mind_nova import request

if typing.TYPE_CHECKING:
    from .mind_core import Mind


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

    async def finish(phase: str, **extra) -> None:
        """统一结束流式事件，并确保事件在返回前刷到服务端。"""
        if not ev_report: return None
        ev_report.emit({"type": phase, "ts": time.time(), **extra})
        await ev_report.flush()

    slog: StreamTyperLogger = StreamTyperLogger(mind.report.log_papers)
    await slog.open()

    first_frame = True

    try:
        async for event in request.stream_chat(mode, model_api, message, filtered_tools, **kwargs):
            if ev_report:
                ev_report.bind_event(event)

            if first_frame:
                await mind.stop_anim()
                first_frame = False

            await slog.start()

            event_type = str(event.get("type") or "")

            if event_type == "turn.start":
                continue

            if event_type == "turn.failed":
                error = str(event.get("error") or "unknown error")
                await slog.feed(chunk=error, display=StreamTyperLogger.BLOCK)
                await slog.stop()
                break

            if event_type == "text.delta":
                text = str(event.get("text") or "")
                await slog.feed(chunk=text, display=StreamTyperLogger.STREAM)
                continue

            if event_type == "text.done":
                continue

            if event_type == "turn.done":
                break

            if event_type == "tool.call":
                name, arguments = event["name"], event.get("arguments", {})

                if Tooling.needs_wakeup(tool_meta, name):
                    if error := await mind.wakeup(session, slog):
                        await slog.feed(error, display=StreamTyperLogger.BLOCK)
                        await slog.stop()
                        await finish(phase="turn.failed", error=str(error))
                        break

                await slog.feed(
                    chunk=f"{name} {arguments}",
                    display=StreamTyperLogger.BLOCK,
                    display_chunk=Tooling.summarize_tool_arguments(name, arguments)
                )

                arguments = Enhancer.exchange(name, arguments, mind.report)

                result = await session.call_tool(name, arguments)
                ok = not result.isError

                enhancer: Enhancer = Enhancer(session, mode, model_api, kwargs.get("metadata"))
                fields = await enhancer.enhance(name, arguments, result, ok, slog)

                await slog.feed(chunk=f"{fields.get('text')}", display=StreamTyperLogger.BLOCK)

                await request.post_tool_result(
                    event["cid"], event["sid"], event["call_id"], name, ok, fields
                )
                continue

            if event_type == "tool.output":
                continue

            continue

    except Exception as e:
        await slog.feed(chunk=str(e), display=StreamTyperLogger.BLOCK)
        await slog.stop()
        await finish(phase="turn.failed", error=f"{type(e).__name__}: {e}")

    await slog.stop()


if __name__ == '__main__':
    pass
