# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mcp import (
    ClientSession,
    types as mcp_types,
)


MAX_TOOL_CATALOG_PAGES = 100
MAX_TOOL_CATALOG_ITEMS = 2048
MAX_TOOL_CATALOG_CURSOR_BYTES = 64 * 1024


async def collect_tool_catalog(session: ClientSession) -> list[mcp_types.Tool]:
    """在所属连接的启动期限内读取全部页，失败或取消时不返回部分目录。"""
    tools: list[mcp_types.Tool] = []
    names: set[str] = set()
    cursors: set[str] = set()
    cursor: str | None = None
    for _ in range(MAX_TOOL_CATALOG_PAGES):
        params = mcp_types.PaginatedRequestParams(cursor=cursor) if cursor is not None else None
        page = await session.list_tools(params=params)
        if len(page.tools) > MAX_TOOL_CATALOG_ITEMS - len(tools):
            raise ValueError("MCP tool catalog exceeds the item limit")
        for tool in page.tools:
            if tool.name in names:
                raise ValueError("MCP tool catalog contains duplicate tool names")
            names.add(tool.name)
        tools.extend(page.tools)
        cursor = page.nextCursor
        if cursor is None:
            return tools
        try:
            cursor_size = len(cursor.encode("utf-8"))
        except UnicodeError:
            raise ValueError("MCP tool catalog returned an invalid cursor") from None
        if cursor_size > MAX_TOOL_CATALOG_CURSOR_BYTES:
            raise ValueError("MCP tool catalog cursor exceeds the byte limit")
        if cursor in cursors:
            raise ValueError("MCP tool catalog returned a repeated cursor")
        cursors.add(cursor)
    raise ValueError("MCP tool catalog exceeds the page limit")


if __name__ == '__main__':
    pass
