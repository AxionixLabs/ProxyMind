# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_nova.stream_events import StreamEvent

ASSISTANT_OUTPUT_BOUNDARY_EVENTS: set[str] = {
    "turn.input.accepted",
    "tool.builtin.call",
    "tool.calls.start",
    "tool.approval_required",
    "tool.approval_review",
    "tool.call",
    "tool.output",
}


def display_event_has_text(event: StreamEvent) -> bool:
    """判断事件是否包含需要展示的 lifecycle 文本。"""
    display = event.display
    if not isinstance(display, dict):
        return False

    for key in ("text", "message", "summary"):
        value = display.get(key)
        if isinstance(value, str) and value.strip():
            return True

    return False


def is_assistant_output_boundary(
    event: StreamEvent,
) -> bool:
    """判断事件是否结束当前 assistant 输出块。"""
    return event.type in ASSISTANT_OUTPUT_BOUNDARY_EVENTS or display_event_has_text(event)


if __name__ == '__main__':
    pass
