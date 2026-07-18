# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass

AppEventKind = typing.Literal[
    "turn.start",
    "turn.thinking",
    "turn.failed",
    "text.delta",
    "text.done",
    "text.meta",
    "tool.builtin.call",
    "tool.builtin.done",
    "tool.calls.start",
    "tool.calls.done",
    "tool.approval_required",
    "tool.call",
    "tool.output",
    "display.block",
    "status.update",
    "lifecycle.display",
    "turn.done"
]


@dataclass(frozen=True, slots=True)
class AppEvent:
    """表示应用层消费的一条结构化事件。"""

    kind: AppEventKind
    text: str = ""
    payload: dict[str, typing.Any] | None = None
    reply: asyncio.Future[str] | None = None


if __name__ == '__main__':
    pass
