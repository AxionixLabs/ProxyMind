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
    event_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)
    protocol_types = {
        "turn.start",
        "text.delta",
        "text.done",
        "tool.call",
        "tool.output",
        "turn.done",
        "turn.failed",
    }

    async def finish(
        status: typing.Literal["completed", "failed"],
        *,
        emit_terminal: bool,
        **extra,
    ) -> None:
        """统一结束流式事件，并确保事件在返回前刷到服务端。"""
        if not event_report:
            return None
        if emit_terminal and status == "failed":
            event_report.emit({
                "type"  : "turn.failed",
                "error" : str(extra.get("error") or "unknown error"),
                "ts"    : time.time()
            })
        await event_report.flush()

    slog: StreamTyperLogger = StreamTyperLogger(mind.report.log_papers)
    await slog.open()

    has_stopped_anim = False
    saw_delta = False
    final_status: typing.Literal["completed", "failed"] = "failed"
    finish_extra: dict[str, typing.Any] = {"error": "stream ended before turn.done"}
    terminal_event_seen = False

    try:
        async for event in request.stream_chat(mode, model_api, message, filtered_tools, **kwargs):
            if not has_stopped_anim:
                await mind.stop_anim()
                has_stopped_anim = True
            await slog.start()

            event_type = str(event.get("type") or "")

            if event_report:
                event_report.bind_event(event)
                if event_type in protocol_types:
                    event_report.emit(event)

            if event_type in {"turn.done", "turn.failed"}:
                terminal_event_seen = True

            match event_type:
                case "turn.start":
                    saw_delta = False
                    terminal_event_seen = False
                    continue

                case "turn.failed":
                    error = str(event.get("error") or "unknown error")
                    await slog.feed(chunk=error, display=StreamTyperLogger.BLOCK)
                    saw_delta = False
                    final_status = "failed"
                    finish_extra = {"error": error}
                    break

                case "text.delta":
                    text = str(event.get("text") or "")
                    saw_delta = True
                    await slog.feed(chunk=text, display=StreamTyperLogger.STREAM)
                    continue

                case "text.done":
                    text = str(event.get("text") or "")
                    if not saw_delta and text:
                        await slog.feed(chunk=text, display=StreamTyperLogger.BLOCK)
                    saw_delta = False
                    continue

                case "turn.done":
                    saw_delta = False
                    final_status = "completed"
                    finish_extra = {}
                    break

                case "tool.call":
                    name, arguments = event["name"], event.get("arguments", {})
                    if Tooling.needs_wakeup(tool_meta, name):
                        if error := await mind.wakeup(session, slog):
                            await slog.feed(error, display=StreamTyperLogger.BLOCK)
                            final_status = "failed"
                            finish_extra = {"error": str(error)}
                            break

                    await slog.feed(
                        chunk=f"{name} {arguments}",
                        display=StreamTyperLogger.BLOCK,
                        display_chunk=Tooling.summarize_tool_arguments(name, arguments),
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

                case "tool.output":
                    continue

                case _:
                    continue

    except Exception as e:
        await slog.feed(chunk=str(e), display=StreamTyperLogger.BLOCK)
        saw_delta = False
        final_status = "failed"
        finish_extra = {"error": f"{type(e).__name__}: {e}"}

    await slog.stop()
    return await finish(final_status, emit_terminal=not terminal_event_seen, **finish_extra)


if __name__ == '__main__':
    pass
