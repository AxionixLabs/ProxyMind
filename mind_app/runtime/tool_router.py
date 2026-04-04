# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mcp import ClientSession
from mcp.types import CallToolResult


def is_hosted_tool(
    tool_meta: dict[str, dict[str, typing.Any]],
    name: str,
    meta: typing.Optional[dict[str, typing.Any]] = None
) -> bool:
    """判断工具是否由服务端注入，而非本地 MCP 注册。"""
    if name in tool_meta:
        return False
    return isinstance(meta, dict) and bool(meta)


async def execute_tool(
    session: ClientSession,
    *,
    tool_meta: dict[str, dict[str, typing.Any]],
    name: str,
    arguments: dict[str, typing.Any],
    meta: typing.Optional[dict[str, typing.Any]] = None
) -> CallToolResult:
    """统一工具执行入口。"""
    if is_hosted_tool(tool_meta, name, meta=meta):
        raise RuntimeError(f"Hosted tool is not configured for local execution: {name}")
    return await session.call_tool(name, arguments)


if __name__ == '__main__':
    pass
