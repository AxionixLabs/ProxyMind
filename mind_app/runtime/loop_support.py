# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.mcp import McpSessionLike
from mind_app.mcp.tool_store import meta_for_tool
from mind_nova.events import EventReport
from engine.tinker import Tooling
from ..stream_ui import StreamUI
from ..stream_events.failure_display import (
    render_failure_display_parts,
    render_failure_text
)
from ..stream_events.finish import finish_stream

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


async def ensure_wakeup(
    mind: "Mind",
    session: McpSessionLike,
    stream_ui: StreamUI,
    *,
    tools: list[dict[str, typing.Any]],
    name: str,
    meta: typing.Optional[dict[str, typing.Any]] = None,
) -> typing.Optional[str]:
    """按工具需要执行 refresh，失败时返回错误文本。"""
    if not Tooling.needs_wakeup(meta_for_tool(tools, name), name, meta=meta):
        return None

    return await mind.wakeup(session, stream_ui)


async def finish_failure(
    stream_ui: StreamUI,
    ev_report: typing.Optional[EventReport],
    *,
    phase: str,
    error: typing.Any,
    **extra: typing.Any
) -> None:
    """统一结束失败事件并输出用户可见错误。"""
    message = "" if error is None else str(error)
    await finish_stream(ev_report, phase=phase, error=message, **extra)
    await stream_ui.end_status(immediate=True)
    await stream_ui.feed(
        render_failure_text(phase, message),
        display=StreamUI.BLOCK,
        display_parts=render_failure_display_parts(phase, message),
        preserve_display_parts=True
    )


if __name__ == '__main__':
    pass
