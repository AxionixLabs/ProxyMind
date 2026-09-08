# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.views.builders.compaction import (
    build_context_compaction_view,
)
from agent.application.views.builders.lifecycle import build_lifecycle_view
from agent.application.views.contracts import PresentationSink
from agent.ports.transcript import TranscriptSink
from protocol.schema.stream_events import (
    ContextCompactionEvent,
    StreamEvent,
)


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
    transcript: TranscriptSink,
) -> bool:
    """展示非核心生命周期事件中的显式内容。"""
    if isinstance(event, ContextCompactionEvent):
        view = build_context_compaction_view(event)
        if view.status == "completed":
            transcript.append(
                "context.compacted",
                actor="system",
                payload={
                    "item_id": view.item_id,
                    "event_seq": view.event_seq,
                    "presentation_epoch": view.presentation_epoch,
                    "phase": view.phase,
                    "trigger": view.trigger,
                    "reason": view.reason,
                    "before_items": view.before_items,
                    "after_items": view.after_items,
                    "before_chars": view.before_chars,
                    "after_chars": view.after_chars,
                    "dropped_items": view.dropped_items,
                    "reduction_ratio": view.reduction_ratio,
                    "latency_ms": view.latency_ms,
                    "replacement_version": view.replacement_version,
                },
            )
        await presentation.emit(view)
        return True
    return await _display_event(
        event,
        presentation=presentation,
    )


if __name__ == '__main__':
    pass
