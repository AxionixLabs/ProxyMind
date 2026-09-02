# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from datetime import timedelta

if typing.TYPE_CHECKING:
    from mcp import types as mcp_types
    from mcp.shared.session import ProgressFnT
    from agent.application.turns.context import TurnContext

__all__ = ("McpSessionPort",)


class McpSessionPort(typing.Protocol):
    """定义工具执行所需的 MCP 会话端口。"""

    def display_name_for_tool(self, name: str) -> str:
        """返回工具目录中模型侧名称对应的用户展示名。"""
        ...

    async def list_tools(self) -> "mcp_types.ListToolsResult":
        """列出当前会话可用的工具。"""
        ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        read_timeout_seconds: timedelta | None = None,
        progress_callback: "ProgressFnT | None" = None,
        *,
        meta: dict[str, typing.Any] | None = None,
        args: dict[str, typing.Any] | None = None,
        call_id: str | None = None,
        turn_context: "TurnContext | None" = None,
        pref_config: typing.Mapping[str, typing.Any] | None = None,
    ) -> "mcp_types.CallToolResult":
        """调用工具并返回 MCP 结果。"""
        ...


if __name__ == '__main__':
    pass
