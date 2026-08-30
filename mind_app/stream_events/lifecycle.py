# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from protocol.schema.stream_events import StreamEvent
from mind_app.output import OutputStatusPort
from mind_app.presentation.contracts import PresentationSink
from mind_app.presentation.lifecycle_views import build_lifecycle_view


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
    status_control: OutputStatusPort
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
    await status_control.begin_reply_wait_status(
        delay_sec=0.15,
        animate_after_sec=0.85,
    )

    return True


async def handle_lifecycle_event(
    event: StreamEvent,
    *,
    presentation: PresentationSink,
    status_control: OutputStatusPort
) -> bool:
    """展示非核心生命周期事件中的显式内容。"""
    return await _display_event(
        event,
        presentation=presentation,
        status_control=status_control,
    )


if __name__ == '__main__':
    pass
