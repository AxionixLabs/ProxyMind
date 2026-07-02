# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from dataclasses import (
    dataclass, field
)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """一次工具调用的原始路由信息。"""

    cid: str
    sid: str
    call_id: str
    name: str
    arguments: dict[str, typing.Any] = field(default_factory=dict)
    meta: dict[str, typing.Any] | None = None
    execution: dict[str, typing.Any] | None = None


@dataclass(slots=True)
class ToolOutput:
    """一次工具调用完成后的回填信息。"""

    call_id: str
    ok: bool
    fields: typing.Union[str, dict[str, typing.Any]]
    text: str = ""
    cost_ms: int = 0


@dataclass(slots=True)
class InFlightTool:
    """正在执行或等待回填的工具任务。"""

    call: ToolCall
    task: asyncio.Task[ToolOutput]
    started_at: float = field(default_factory=time.time)


if __name__ == '__main__':
    pass
