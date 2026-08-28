# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from mcp import types as mcp_types
from mind_app.client_tools.types import (
    ClientToolRuntime,
    NESTED_TOOL_DISPATCH_META_KEY,
    TURN_INTERRUPT_META_KEY,
)
from mind_app.runtime.execution import TurnContext
from .types import BuiltinTool


class BuiltinToolRegistry:
    """注册并分发核心内置工具，不与普通客户端工具混用。"""

    def __init__(self, tools: typing.Iterable[BuiltinTool] | None = None) -> None:
        self._tools: dict[str, BuiltinTool] = {}
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: BuiltinTool) -> None:
        """按名称注册一个核心内置工具。"""
        name = str(tool.name or "").strip()
        if not name:
            raise ValueError("builtin tool name is required")
        if name in self._tools:
            raise ValueError(f"duplicate builtin tool: {name}")
        self._tools[name] = tool

    def has_tool(self, name: str) -> bool:
        """判断指定核心内置工具是否存在。"""
        return str(name or "").strip() in self._tools

    def list_tools(self) -> mcp_types.ListToolsResult:
        """返回核心内置工具的 MCP 描述。"""
        return mcp_types.ListToolsResult(
            tools=[tool.to_mcp_tool() for tool in self._tools.values()]
        )

    async def call_tool(
        self,
        session: typing.Any,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        *,
        read_timeout_seconds: typing.Any = None,
        progress_callback: typing.Any = None,
        meta: dict[str, typing.Any] | None = None,
        call_id: str | None = None,
        turn_context: TurnContext | None = None,
        pref_config: typing.Mapping[str, typing.Any] | None = None,
    ) -> mcp_types.CallToolResult:
        """分发一次核心内置工具调用。"""
        tool = self._tools.get(str(name or "").strip())
        if tool is None:
            raise KeyError(f"unknown builtin tool: {name}")
        if turn_context is None:
            raise TypeError("builtin tool turn context is required")
        if not isinstance(pref_config, typing.Mapping):
            raise TypeError("builtin tool preference config is required")

        runtime_meta = dict(meta or {})
        nested_dispatch = runtime_meta.pop(
            NESTED_TOOL_DISPATCH_META_KEY,
            None,
        )
        turn_interrupt = runtime_meta.pop(
            TURN_INTERRUPT_META_KEY,
            None,
        )
        runtime = ClientToolRuntime(
            session=session,
            turn_context=turn_context,
            pref_config=copy.deepcopy(dict(pref_config)),
            read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
            meta=runtime_meta or None,
            call_id=call_id,
            nested_tool_dispatch=(
                nested_dispatch if callable(nested_dispatch) else None
            ),
            interrupt_turn=(
                turn_interrupt if callable(turn_interrupt) else None
            ),
        )
        return await tool.handler(dict(arguments or {}), runtime)


if __name__ == '__main__':
    pass
