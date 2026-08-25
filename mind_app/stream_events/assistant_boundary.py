# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_nova.stream_events import StreamEvent

ASSISTANT_OUTPUT_BOUNDARY_EVENTS: frozenset[str] = frozenset({
    "turn.input.accepted",
    "tool.builtin.call",
    "tool.calls.start",
    "tool.approval_required",
    "tool.approval_review",
    "tool.call",
    "tool.output",
})


def is_assistant_output_boundary(
    event: StreamEvent,
) -> bool:
    """判断结构化事件是否明确结束当前 assistant 输出块。"""
    return event.type in ASSISTANT_OUTPUT_BOUNDARY_EVENTS


if __name__ == '__main__':
    pass
