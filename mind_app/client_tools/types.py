# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from mcp import types as mcp_types

if typing.TYPE_CHECKING:
    from agent.application.execution import TurnContext

NestedToolDispatch = typing.Callable[
    [str, dict[str, typing.Any], str],
    typing.Awaitable[mcp_types.CallToolResult]
]

TurnInterrupt = typing.Callable[
    [str],
    typing.Awaitable[bool],
]

NESTED_TOOL_DISPATCH_META_KEY = "_nested_tool_dispatch"
TURN_INTERRUPT_META_KEY = "_turn_interrupt"


@dataclass(slots=True)
class ClientToolRuntime:
    """客户端工具处理函数可使用的运行上下文。"""
    session: typing.Any
    turn_context: "TurnContext"
    pref_config: typing.Mapping[str, typing.Any]
    read_timeout_seconds: typing.Any = None
    progress_callback: typing.Any = None
    meta: dict[str, typing.Any] | None = None
    call_id: str | None = None
    nested_tool_dispatch: NestedToolDispatch | None = None
    interrupt_turn: TurnInterrupt | None = None


ClientToolHandler = typing.Callable[
    [
        dict[str, typing.Any],
        ClientToolRuntime,
    ],
    typing.Awaitable[mcp_types.CallToolResult],
]


@dataclass(frozen=True, slots=True)
class ClientTool:
    """客户端工具的描述信息与处理函数。"""
    name: str
    description: str
    input_schema: dict[str, typing.Any]
    handler: ClientToolHandler
    meta: dict[str, typing.Any] = field(default_factory=dict)

    def to_mcp_tool(self) -> mcp_types.Tool:
        """转换为 MCP 兼容的工具描述。"""
        meta: dict[str, typing.Any] = {
            "client_builtin": True,
            **dict(self.meta or {}),
        }
        return mcp_types.Tool.model_validate({
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema or {}),
            "_meta": meta,
        })


if __name__ == '__main__':
    pass
