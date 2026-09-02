# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import dataclass
from datetime import timedelta

from agent.ports.javascript import NestedToolDispatch
from agent.ports.mcp_session import McpSessionPort

if typing.TYPE_CHECKING:
    from agent.application.turns.context import TurnContext

__all__ = (
    "NESTED_TOOL_DISPATCH_META_KEY",
    "NestedToolDispatch",
    "TURN_INTERRUPT_META_KEY",
    "ToolHandlerContext",
    "ToolProgressCallback",
    "TurnInterrupt",
)


ToolProgressCallback: typing.TypeAlias = Callable[
    [float, float | None, str | None],
    Awaitable[None],
]
TurnInterrupt: typing.TypeAlias = Callable[[str], Awaitable[bool]]

NESTED_TOOL_DISPATCH_META_KEY = "_nested_tool_dispatch"
TURN_INTERRUPT_META_KEY = "_turn_interrupt"


@dataclass(slots=True)
class ToolHandlerContext:
    """保存一次本地工具处理所需的会话、Turn 和调用级依赖。

    注册表负责创建该对象；工具处理器只能在当前调用生命周期内使用其中的会话与
    回调，不得把它们保存为跨 Turn 状态。
    """

    session: McpSessionPort
    turn_context: "TurnContext"
    pref_config: Mapping[str, typing.Any]
    read_timeout_seconds: timedelta | None = None
    progress_callback: ToolProgressCallback | None = None
    meta: dict[str, typing.Any] | None = None
    call_id: str | None = None
    nested_tool_dispatch: NestedToolDispatch | None = None
    interrupt_turn: TurnInterrupt | None = None


if __name__ == '__main__':
    pass
