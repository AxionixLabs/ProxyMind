# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

if typing.TYPE_CHECKING:
    from mcp import types as mcp_types
    from mind_app.runtime.execution import TurnContext


class McpSessionLike(typing.Protocol):
    """描述可被运行时使用的 MCP 会话接口。"""

    async def list_tools(self) -> "mcp_types.ListToolsResult":
        """列出当前会话可用的 MCP 工具。"""
        ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        read_timeout_seconds: typing.Any = None,
        progress_callback: typing.Any = None,
        *,
        meta: dict[str, typing.Any] | None = None,
        args: dict[str, typing.Any] | None = None,
        call_id: str | None = None,
        turn_context: "TurnContext | None" = None,
        pref_config: typing.Mapping[str, typing.Any] | None = None
    ) -> "mcp_types.CallToolResult":
        """调用指定 MCP 工具并返回执行结果。"""
        ...


if __name__ == '__main__':
    pass
