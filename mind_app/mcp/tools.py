# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from engine.observability import observe
from mcp import ClientSession
from mcp import types as mcp_types
from .contracts import McpSessionLike
from .session_adapter import CompositeToolSession


@dataclass(frozen=True)
class McpToolContext:
    """承载一次模型调用所需的 MCP 会话和工具描述。"""
    session: McpSessionLike
    tools: list[dict[str, typing.Any]]


def _wire_effect_hint(tool: mcp_types.Tool) -> dict[str, str]:
    """把工具声明转换为服务端可严格校验的效果提示。"""
    meta = dict(tool.meta or {})

    explicit = meta.get("effect")
    if isinstance(explicit, dict):
        return {
            "scope": str(explicit.get("scope") or ""),
            "class": str(explicit.get("class") or ""),
            "replay_policy": str(explicit.get("replay_policy") or ""),
        }

    annotations = tool.annotations
    if annotations is not None and annotations.readOnlyHint is True:
        return {"scope": "none", "class": "read_only", "replay_policy": "safe"}

    return {
        "scope": "external",
        "class": "non_replayable",
        "replay_policy": "manual",
    }


def build_wire_tools(
    list_tools: mcp_types.ListToolsResult,
) -> list[dict[str, typing.Any]]:
    """把 MCP 工具列表转换为传输层工具描述，保留 MCP schema 和 meta。"""
    tools: list[dict[str, typing.Any]] = []

    for tool in list_tools.tools:
        meta = dict(tool.meta or {})
        if bool(meta.get("hidden", False)):
            continue
        meta["effect"] = _wire_effect_hint(tool)

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
    service_session: ClientSession | None = None,
    external_group: typing.Any = None,
    client_registry: typing.Any = None,
) -> McpToolContext:
    """合并可用工具来源，并生成模型调用上下文。"""
    active_session = CompositeToolSession(
        service_session,
        external_group,
        client_registry=client_registry
    )

    list_tools = await active_session.list_tools()
    tools      = build_wire_tools(list_tools)

    return McpToolContext(
        session=active_session,
        tools=tools
    )


if __name__ == '__main__':
    pass
