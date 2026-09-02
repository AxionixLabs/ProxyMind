# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from datetime import timedelta

from mcp import types as mcp_types
from mcp.shared.session import ProgressFnT

from agent.ports import (
    ExternalToolGroupPort,
    McpSessionPort,
    ToolRegistryPort,
)
from agent.domain.approvals import McpToolDescriptor
from infrastructure.mcp.approval import prepare_mcp_approval_descriptor
from infrastructure.mcp.external_status import should_reraise_external
from infrastructure.mcp.values import truncate_text
from observability import observe_exception

if typing.TYPE_CHECKING:
    from agent.application.turns.context import TurnContext


class CompositeToolSession(McpSessionPort):
    """合并内置、客户端、外部和服务 MCP 会话，并按工具来源分发调用。"""

    def __init__(
        self,
        service_session: McpSessionPort | None = None,
        external_group: ExternalToolGroupPort | None = None,
        client_registry: ToolRegistryPort | None = None,
        builtin_registry: ToolRegistryPort | None = None,
    ) -> None:
        """保存可选服务会话、外部工具分组及两类本地工具。"""
        self.service_session = service_session
        self.external_group = external_group
        self.client_registry = client_registry
        self.builtin_registry = builtin_registry

    def display_name_for_tool(self, name: str) -> str:
        """把外部工具限定名投影为服务声明的原始名称。"""
        if self.external_group is None:
            return name
        tool = self.external_group.tools.get(name)
        if tool is None:
            return name
        return str(tool.name or name).strip() or name

    @staticmethod
    def tool_for_openai(
        tool: mcp_types.Tool,
        *,
        display_name: str,
        description: str,
        meta: dict[str, typing.Any]
    ) -> mcp_types.Tool:
        """生成用于模型侧展示的工具对象，保留原始输入 schema。"""
        return tool.model_copy(
            update={
                "name": display_name,
                "description": truncate_text(description, 2048),
                "inputSchema": tool.inputSchema,
                "meta": meta
            }
        )

    async def list_tools(self) -> mcp_types.ListToolsResult:
        """返回全部可用工具合并后的工具列表。"""
        client_names: set[str] = set()
        tools: list[mcp_types.Tool] = []

        if self.builtin_registry is not None:
            builtin_result = self.builtin_registry.list_tools()
            tools.extend(builtin_result.tools)
            client_names.update(tool.name for tool in builtin_result.tools)

        if self.client_registry is not None:
            client_result = self.client_registry.list_tools()
            tools.extend(client_result.tools)
            client_names.update(tool.name for tool in client_result.tools)

        if self.service_session is not None:
            local_result = await self.service_session.list_tools()

            tools.extend(
                tool for tool in local_result.tools
                if tool.name not in client_names
            )

        if self.external_group is not None:
            try:
                for display_name, tool in self.external_group.tools.items():
                    alias = (
                        display_name.split("__", 2)[1]
                        if display_name.startswith("mcp__")
                        else "external"
                    )

                    meta = dict(tool.meta or {})
                    meta["external"] = True
                    meta["server"] = alias
                    meta["transport"] = str(meta.get("transport") or "external").strip().lower()

                    description = str(tool.description or "").strip()
                    prefix = f"[External {alias}]"
                    full_description = f"{prefix} {description}".strip() if description else prefix

                    tools.append(
                        self.tool_for_openai(
                            tool,
                            display_name=display_name,
                            description=full_description,
                            meta=meta
                        )
                    )
            except BaseException as exc:
                if should_reraise_external(exc):
                    raise
                observe_exception(
                    "external_mcp.tools.failed",
                    exc,
                    level="WARNING",
                )

        return mcp_types.ListToolsResult(tools=tools)

    def mcp_approval_descriptor(
        self,
        name: str,
        arguments: dict[str, typing.Any],
    ) -> McpToolDescriptor | None:
        """校验外部 MCP 调用并返回类型化审批描述符。"""
        if self.external_group is None:
            return None
        tool = self.external_group.tools.get(name)
        if tool is None:
            return None
        return prepare_mcp_approval_descriptor(name, tool, arguments)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        read_timeout_seconds: timedelta | None = None,
        progress_callback: ProgressFnT | None = None,
        *,
        meta: dict[str, typing.Any] | None = None,
        args: dict[str, typing.Any] | None = None,
        call_id: str | None = None,
        turn_context: "TurnContext | None" = None,
        pref_config: typing.Mapping[str, typing.Any] | None = None
    ) -> mcp_types.CallToolResult:
        """根据工具名称选择外部会话或本地会话执行调用。"""
        payload = arguments if args is None else args

        if self.builtin_registry is not None and self.builtin_registry.has_tool(name):
            return await self.builtin_registry.call_tool(
                self,
                name,
                payload,
                read_timeout_seconds=read_timeout_seconds,
                progress_callback=progress_callback,
                meta=meta,
                call_id=call_id,
                turn_context=turn_context,
                pref_config=pref_config,
            )

        if self.client_registry is not None and self.client_registry.has_tool(name):
            return await self.client_registry.call_tool(
                self,
                name,
                payload,
                read_timeout_seconds=read_timeout_seconds,
                progress_callback=progress_callback,
                meta=meta,
                call_id=call_id,
                turn_context=turn_context,
                pref_config=pref_config,
            )

        if self.external_group is not None and name in self.external_group.tools:
            if call_id is not None and not str(call_id).strip():
                raise ValueError("external MCP call_id must be non-empty")
            return await self.external_group.call_tool(
                name,
                payload,
                read_timeout_seconds=read_timeout_seconds,
                progress_callback=progress_callback,
                meta=meta
            )

        if self.service_session is not None:
            return await self.service_session.call_tool(
                name,
                payload,
                read_timeout_seconds=read_timeout_seconds,
                progress_callback=progress_callback,
                meta=meta
            )

        raise KeyError(f"unknown tool: {name}")


if __name__ == '__main__':
    pass
