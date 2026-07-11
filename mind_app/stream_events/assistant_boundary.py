# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

ASSISTANT_OUTPUT_BOUNDARY_EVENTS: set[str] = {
    "tool.builtin.call",
    "tool.calls.start",
    "tool.approval_required",
    "tool.call",
    "tool.output",
}


def display_event_has_text(event: dict[str, typing.Any]) -> bool:
    """判断事件是否包含需要展示的 lifecycle 文本。"""
    display = event.get("display")
    if not isinstance(display, dict):
        return False

    for key in ("text", "message", "summary"):
        value = display.get(key)
        if isinstance(value, str) and value.strip():
            return True

    return False


def is_assistant_output_boundary(
    event_type: str,
    event: dict[str, typing.Any]
) -> bool:
    """判断事件是否结束当前 assistant 输出块。"""
    return event_type in ASSISTANT_OUTPUT_BOUNDARY_EVENTS or display_event_has_text(event)


if __name__ == '__main__':
    pass
