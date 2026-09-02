# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.views.builders.lifecycle import build_lifecycle_view
from agent.application.views.contracts import PresentationSink
from protocol.schema.stream_events import StreamEvent


def _display_text(display: dict[str, typing.Any]) -> str:
    """提取 display 中可展示的文本。"""
    for key in ("text", "message", "summary"):
        value = display.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


async def _display_event(
    event: StreamEvent,
    *,
    presentation: PresentationSink,
) -> bool:
    """展示服务端显式声明需要显示的事件内容。"""
    if not isinstance(display := event.display, dict):
        return False

    if not (text := _display_text(display)):
        return False

    view = build_lifecycle_view(text)
    if view is None:
        return False

    await presentation.emit(view)
    return True


async def handle_lifecycle_event(
    event: StreamEvent,
    *,
    presentation: PresentationSink,
) -> bool:
    """展示非核心生命周期事件中的显式内容。"""
    return await _display_event(
        event,
        presentation=presentation,
    )


if __name__ == '__main__':
    pass
