# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from loguru import logger
from mcp import ClientSession, types as mcp_types
from .config import truncate_text
from .status import (
    should_reraise_external, summarize_exception
)


class McpSessionLike(typing.Protocol):
    """描述可被运行时使用的 MCP 会话接口。"""

    async def list_tools(self) -> mcp_types.ListToolsResult:
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
        args: dict[str, typing.Any] | None = None
    ) -> mcp_types.CallToolResult:
        """调用指定 MCP 工具并返回执行结果。"""
        ...


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
            "name"        : display_name,
            "description" : truncate_text(description, 2048),
            "inputSchema" : tool.inputSchema,
            "meta"        : meta
        }
    )


class MultiMcpSession:
    """合并本地与外部 MCP 会话，并按工具来源分发调用。"""

    def __init__(
        self,
        local_session: ClientSession,
        external_group: typing.Any = None,
    ) -> None:
        """保存本地会话和可选的外部工具分组。"""
        self.local_session = local_session
        self.external_group = external_group

    async def list_tools(self) -> mcp_types.ListToolsResult:
        """返回本地工具和外部工具合并后的工具列表。"""
        local_result = await self.local_session.list_tools()
        tools = list(local_result.tools)

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
                        tool_for_openai(
                            tool,
                            display_name=display_name,
                            description=full_description,
                            meta=meta
                        )
                    )
            except BaseException as exc:
                if should_reraise_external(exc):
                    raise
                logger.debug(
                    f"[MCP] external tools skipped {summarize_exception(exc)}"
                )

        return mcp_types.ListToolsResult(tools=tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        read_timeout_seconds: typing.Any = None,
        progress_callback: typing.Any = None,
        *,
        meta: dict[str, typing.Any] | None = None,
        args: dict[str, typing.Any] | None = None
    ) -> mcp_types.CallToolResult:
        """根据工具名称选择外部会话或本地会话执行调用。"""
        payload = arguments if args is None else args

        if self.external_group is not None and name in self.external_group.tools:
            return await self.external_group.call_tool(
                name,
                payload,
                read_timeout_seconds=read_timeout_seconds,
                progress_callback=progress_callback,
                meta=meta
            )

        return await self.local_session.call_tool(
            name,
            payload,
            read_timeout_seconds=read_timeout_seconds,
            progress_callback=progress_callback,
            meta=meta
        )


if __name__ == '__main__':
    pass
