# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import inspect
import typing
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)

from agent.ports import (
    CapabilityError,
    McpCapability,
)
from agent.protocol import (
    McpToolDefinition,
    McpToolResult,
)
from agent.protocol.json_value import (
    JsonValue,
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)

McpToolHandler: typing.TypeAlias = Callable[
    [Mapping[str, ThawedJsonValue]],
    McpToolResult | Awaitable[McpToolResult],
]


class InMemoryMcpCapability:
    """提供可脱离网络 SDK 验证的 MCP 工具发现与调用能力。"""

    def __init__(
        self,
        tools: typing.Iterable[McpToolDefinition],
        handlers: Mapping[str, McpToolHandler],
    ) -> None:
        """绑定不可变工具目录和显式调用处理器。"""
        definitions = tuple(tools)
        by_name: dict[str, McpToolDefinition] = {}
        for tool in definitions:
            if not isinstance(tool, McpToolDefinition):
                raise TypeError("MCP tools must be McpToolDefinition values")
            if tool.name in by_name:
                raise ValueError(f"duplicate MCP tool: {tool.name}")
            by_name[tool.name] = tool

        normalized_handlers: dict[str, McpToolHandler] = {}
        for name, handler in handlers.items():
            tool_name = str(name or "").strip()
            if not tool_name:
                raise ValueError("MCP handler name is required")
            if not callable(handler):
                raise TypeError("MCP handler must be callable")
            normalized_handlers[tool_name] = handler

        unknown_handlers = set(normalized_handlers) - set(by_name)
        if unknown_handlers:
            names = ", ".join(sorted(unknown_handlers))
            raise ValueError(f"MCP handlers have no tool definition: {names}")

        self._tools = by_name
        self._handlers = normalized_handlers
        self._closed = False

    async def list_tools(self) -> tuple[McpToolDefinition, ...]:
        """返回按登记顺序稳定排序的工具定义快照。"""
        self._require_open()
        return tuple(self._tools.values())

    async def call_tool(
        self,
        name: str,
        *,
        arguments: Mapping[str, JsonValue] | None = None,
        call_id: str | None = None,
    ) -> McpToolResult:
        """校验调用身份后执行内存处理器并归一化结果。"""
        self._require_open()
        tool_name = str(name or "").strip()
        if not tool_name or tool_name not in self._tools:
            raise CapabilityError(
                "mcp_tool_not_found",
                f"MCP tool is not registered: {tool_name or '<empty>'}",
            )
        if call_id is not None and not str(call_id).strip():
            raise CapabilityError(
                "mcp_call_id_invalid",
                "MCP call_id must be non-empty when provided",
            )

        raw_arguments: Mapping[str, JsonValue] = arguments or {}
        if not isinstance(raw_arguments, Mapping):
            raise CapabilityError(
                "mcp_arguments_invalid",
                "MCP arguments must be an object",
            )
        frozen_arguments = freeze_json(
            dict(raw_arguments),
            field_name="MCP arguments",
        )
        if not isinstance(frozen_arguments, Mapping):
            raise CapabilityError(
                "mcp_arguments_invalid",
                "MCP arguments must be an object",
            )

        handler = self._handlers.get(tool_name)
        if handler is None:
            raise CapabilityError(
                "mcp_tool_unavailable",
                f"MCP tool has no callable handler: {tool_name}",
            )

        try:
            result = handler(thaw_object(frozen_arguments, field_name="MCP arguments"))
            if inspect.isawaitable(result):
                result = await result
        except CapabilityError:
            raise
        except Exception as error:
            raise CapabilityError(
                "mcp_tool_failed",
                str(error).strip() or "MCP tool call failed",
                details={"exception_type": type(error).__name__},
            ) from error

        if not isinstance(result, McpToolResult):
            raise CapabilityError(
                "mcp_result_invalid",
                "MCP handler returned an invalid result",
            )
        return result

    async def aclose(self) -> None:
        """幂等关闭内存能力，后续操作统一拒绝。"""
        self._closed = True

    def _require_open(self) -> None:
        """拒绝在关闭后继续访问能力。"""
        if self._closed:
            raise CapabilityError("mcp_closed", "MCP capability is closed")


if not isinstance(InMemoryMcpCapability([], {}), McpCapability):
    raise TypeError("InMemoryMcpCapability must implement McpCapability")

if __name__ == '__main__':
    pass
