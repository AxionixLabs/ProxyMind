# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from loguru import logger
from mcp import ClientSession
from mcp import types as mcp_types
from .session_adapter import (
    McpSessionLike, MultiMcpSession
)


@dataclass(frozen=True)
class McpToolContext:
    """承载一次模型调用所需的 MCP 会话和工具描述。"""
    session: McpSessionLike
    openai_tools: list[dict[str, typing.Any]]
    tool_meta: dict[str, dict[str, typing.Any]]


def build_openai_tools(
    list_tools: mcp_types.ListToolsResult,
) -> tuple[list[dict[str, typing.Any]], dict[str, dict[str, typing.Any]]]:
    """把 MCP 工具列表转换为 OpenAI 兼容的工具描述。"""
    openai_tools: list[dict[str, typing.Any]] = []
    tool_meta: dict[str, dict[str, typing.Any]] = {}

    for tool in list_tools.tools:
        meta = dict(tool.meta or {})
        if bool(meta.get("hidden", False)):
            continue
        tool_meta[tool.name] = meta

        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                }
            }
        )

    logger.debug(f"[Tooling] count={len(openai_tools)}")

    return openai_tools, tool_meta


async def build_tool_context(
    local_session: ClientSession,
    external_group: typing.Any = None,
) -> McpToolContext:
    """合并本地与外部 MCP 工具，并生成模型调用上下文。"""
    active_session = MultiMcpSession(local_session, external_group)
    list_tools = await active_session.list_tools()
    openai_tools, tool_meta = build_openai_tools(list_tools)

    return McpToolContext(
        session=active_session,
        openai_tools=openai_tools,
        tool_meta=tool_meta,
    )


if __name__ == '__main__':
    pass
