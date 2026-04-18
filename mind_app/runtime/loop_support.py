# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mcp import ClientSession
from mind_nova.events import EventReport
from engine.tinker import Tooling
from ..stream_ui import StreamUI
from ..stream_events.finish import finish_stream

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


async def ensure_wakeup(
    mind: "Mind",
    session: ClientSession,
    stream_ui: StreamUI,
    *,
    tool_meta: dict[str, dict[str, typing.Any]],
    name: str,
    meta: typing.Optional[dict[str, typing.Any]] = None,
) -> typing.Optional[str]:
    """按工具需要执行 refresh，失败时返回错误文本。"""
    if not Tooling.needs_wakeup(tool_meta, name, meta=meta):
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
    await stream_ui.feed(f"{message}\n", display=StreamUI.BLOCK)


if __name__ == '__main__':
    pass
