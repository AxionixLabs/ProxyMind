# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)
from mcp import types as mcp_types
from mind_app.client_tools.types import ClientToolRuntime

BuiltinToolHandler = typing.Callable[
    [dict[str, typing.Any], ClientToolRuntime],
    typing.Awaitable[mcp_types.CallToolResult],
]


@dataclass(frozen=True, slots=True)
class BuiltinTool:
    """保存核心内置工具的描述和执行入口。"""
    name: str
    description: str
    input_schema: dict[str, typing.Any]
    handler: BuiltinToolHandler
    meta: dict[str, typing.Any] = field(default_factory=dict)

    def to_mcp_tool(self) -> mcp_types.Tool:
        """转换为模型侧使用的 MCP 工具描述。"""
        meta: dict[str, typing.Any] = {
            "builtin": True,
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
