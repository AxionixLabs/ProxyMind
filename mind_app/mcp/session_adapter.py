# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from loguru import logger
from mcp import ClientSession, types as mcp_types
from .config import (
    normalize_tool_schema, truncate_text
)
from .status import (
    should_reraise_external, summarize_exception
)


class McpSessionLike(typing.Protocol):

    async def list_tools(self) -> mcp_types.ListToolsResult:
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
        ...


def tool_for_openai(
    tool: mcp_types.Tool,
    *,
    display_name: str,
    description: str,
    meta: dict[str, typing.Any]
) -> mcp_types.Tool:
    return tool.model_copy(
        update={
            "name"        : display_name,
            "description" : truncate_text(description, 2048),
            "inputSchema" : normalize_tool_schema(tool.inputSchema),
            "meta"        : meta
        }
    )


class MultiMcpSession(object):

    def __init__(
        self,
        local_session: ClientSession,
        external_group: typing.Any = None,
    ) -> None:
        self.local_session = local_session
        self.external_group = external_group

    async def list_tools(self) -> mcp_types.ListToolsResult:
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
