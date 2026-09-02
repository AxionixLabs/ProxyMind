# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from mcp import types as mcp_types

from agent.ports import (
    ExternalToolGroupPort,
    McpSessionPort,
    ToolRegistryPort,
)
from infrastructure.mcp.composite_session import CompositeToolSession
from observability import observe


@dataclass(frozen=True)
class McpToolContext:
    """承载一次模型调用所需的 MCP 会话和工具描述。"""
    session: McpSessionPort
    tools: list[dict[str, typing.Any]]


def build_wire_tools(list_tools: mcp_types.ListToolsResult) -> list[dict[str, typing.Any]]:
    """把 MCP 工具列表转换为传输层工具描述，保留 MCP schema 和 meta。"""
    tools: list[dict[str, typing.Any]] = []

    for tool in list_tools.tools:
        meta = dict(tool.meta or {})
        if bool(meta.get("hidden", False)):
            continue

        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
                "meta": meta
            }
        )

    observe("tools.catalog.ready", count=len(tools))

    return tools


async def build_tool_context(
    *,
    service_session: McpSessionPort | None = None,
    external_group: ExternalToolGroupPort | None = None,
    client_registry: ToolRegistryPort | None = None,
    builtin_registry: ToolRegistryPort | None = None,
) -> McpToolContext:
    """合并可用工具来源，并生成模型调用上下文。"""
    active_session = CompositeToolSession(
        service_session=service_session,
        external_group=external_group,
        client_registry=client_registry,
        builtin_registry=builtin_registry,
    )

    list_tools = await active_session.list_tools()
    tools = build_wire_tools(list_tools)

    return McpToolContext(
        session=active_session,
        tools=tools
    )


if __name__ == '__main__':
    pass
