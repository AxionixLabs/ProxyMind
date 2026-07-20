# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_app.output import OutputControlPort
from mind_app.presentation.contracts import PresentationSink
from mind_app.presentation.lifecycle_views import build_lifecycle_view

if typing.TYPE_CHECKING:
    from mind_app.mcp.contracts import McpSessionLike
    from mind_app.controller import Mind
    from mind_app.stream_state.segment import SegmentTracker


@dataclass(slots=True)
class StreamEventContext:
    """流式事件处理上下文。"""

    mind: "Mind"
    session: "McpSessionLike"
    output_control: OutputControlPort
    presentation: PresentationSink
    tracker: "SegmentTracker"
    mode: str
    pref_config: dict[str, typing.Any]
    metadata: dict[str, typing.Any]


LifecycleHandler = typing.Callable[
    [dict[str, typing.Any], StreamEventContext],
    typing.Awaitable[bool]
]

LIFECYCLE_HANDLERS: dict[str, LifecycleHandler] = {}


def _display_text(display: dict[str, typing.Any]) -> str:
    """提取 display 中可展示的文本。"""
    for key in ("text", "message", "summary"):
        value = display.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


async def _display_event(
    event: dict[str, typing.Any],
    ctx: StreamEventContext
) -> bool:
    """展示服务端显式声明需要显示的事件内容。"""
    if not isinstance(display := event.get("display"), dict):
        return False

    if not (text := _display_text(display)):
        return False

    view = build_lifecycle_view(text)
    if view is None:
        return False

    await ctx.presentation.emit(view)
    await ctx.output_control.begin_reply_wait_status(delay_sec=0.15, animate_after_sec=0.85)

    return True


async def handle_lifecycle_event(
    event_type: str,
    event: dict[str, typing.Any],
    ctx: StreamEventContext,
) -> bool:
    """分发已注册的非核心生命周期事件。"""
    handler = LIFECYCLE_HANDLERS.get(event_type)
    if handler is not None:
        return await handler(event, ctx)

    return await _display_event(event, ctx)


if __name__ == '__main__':
    pass
