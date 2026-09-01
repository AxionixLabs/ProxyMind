# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import copy
import enum
import typing
from collections.abc import (
    Iterable,
    Mapping,
)
from datetime import timedelta
from mcp import types as mcp_types
from mcp.shared.session import ProgressFnT
from agent.application.tools.context import (
    NESTED_TOOL_DISPATCH_META_KEY,
    TURN_INTERRUPT_META_KEY,
    ToolHandlerContext,
)
from agent.application.tools.definitions import (
    BuiltinTool,
    ClientTool,
    LocalToolDefinition,
)
from agent.application.turns.context import TurnContext
from agent.ports.mcp_session import McpSessionPort


class ToolRegistrySource(enum.StrEnum):
    """标识一个本地工具注册表的唯一工具来源。"""

    CLIENT = "client"
    BUILTIN = "builtin"


def _definition_source(definition: LocalToolDefinition) -> ToolRegistrySource:
    """返回工具定义对应的注册表来源。"""
    if isinstance(definition, ClientTool):
        return ToolRegistrySource.CLIENT
    if isinstance(definition, BuiltinTool):
        return ToolRegistrySource.BUILTIN
    raise TypeError("local tool definition is required")


class ToolRegistry:
    """注册并分发同一来源的本地工具，并适配 MCP 工具模型。"""

    def __init__(
        self,
        tools: Iterable[LocalToolDefinition] | None = None,
    ) -> None:
        self._tools: dict[str, LocalToolDefinition] = {}
        self._source: ToolRegistrySource | None = None
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: LocalToolDefinition) -> None:
        """注册一个本地工具，并拒绝来源混用或名称冲突。"""
        source = _definition_source(tool)
        if self._source is not None and self._source is not source:
            raise ValueError("local tool registry cannot mix tool sources")

        name = str(tool.name or "").strip()
        if not name:
            raise ValueError(f"{source.value} tool name is required")
        if name in self._tools:
            raise ValueError(f"duplicate {source.value} tool: {name}")

        self._source = source
        self._tools[name] = tool

    def has_tool(self, name: str) -> bool:
        """判断指定本地工具是否存在。"""
        return str(name or "").strip() in self._tools

    @staticmethod
    def _to_mcp_tool(tool: LocalToolDefinition) -> mcp_types.Tool:
        """在 MCP adapter 边界生成模型侧工具描述。"""
        source = _definition_source(tool)
        marker = "client_builtin" if source is ToolRegistrySource.CLIENT else "builtin"
        meta = dict(tool.meta)
        meta[marker] = True
        return mcp_types.Tool.model_validate({
            "name": tool.name,
            "description": tool.description,
            "inputSchema": dict(tool.input_schema),
            "_meta": meta,
        })

    def list_tools(self) -> mcp_types.ListToolsResult:
        """返回当前本地工具的 MCP 兼容描述。"""
        return mcp_types.ListToolsResult(
            tools=[self._to_mcp_tool(tool) for tool in self._tools.values()],
        )

    async def call_tool(
        self,
        session: McpSessionPort,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        *,
        read_timeout_seconds: timedelta | None = None,
        progress_callback: ProgressFnT | None = None,
        meta: dict[str, typing.Any] | None = None,
        call_id: str | None = None,
        turn_context: TurnContext | None = None,
        pref_config: Mapping[str, typing.Any] | None = None,
    ) -> mcp_types.CallToolResult:
        """创建调用级上下文并分发一个本地工具。"""
        tool = self._tools.get(str(name or "").strip())
        source = self._source.value if self._source is not None else "local"
        if tool is None:
            raise KeyError(f"unknown {source} tool: {name}")
        if not isinstance(turn_context, TurnContext):
            raise TypeError(f"{source} tool turn context is required")
        if not isinstance(pref_config, Mapping):
            raise TypeError(f"{source} tool preference config is required")

        runtime_meta = dict(meta or {})
        nested_dispatch = runtime_meta.pop(NESTED_TOOL_DISPATCH_META_KEY, None)
        turn_interrupt = runtime_meta.pop(TURN_INTERRUPT_META_KEY, None)
        context = ToolHandlerContext(
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
            interrupt_turn=(turn_interrupt if callable(turn_interrupt) else None),
        )
        return await tool.handler(dict(arguments or {}), context)


__all__ = (
    "ToolRegistry",
    "ToolRegistrySource",
)
