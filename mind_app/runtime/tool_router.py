# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.mcp import McpSessionLike
from mcp.types import CallToolResult
from .mcp_notify import (
    emit_tool_progress,
    supports_tool_progress,
)


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
    session: McpSessionLike,
    *,
    tool_meta: dict[str, dict[str, typing.Any]],
    name: str,
    arguments: dict[str, typing.Any],
    meta: typing.Optional[dict[str, typing.Any]] = None,
    stream_callback: typing.Optional[typing.Callable[[str], typing.Awaitable[None]]] = None,
    enable_progress_notify: bool = False
) -> CallToolResult:
    """统一工具执行入口。"""
    if is_hosted_tool(tool_meta, name, meta=meta):
        raise RuntimeError(f"Hosted tool is not configured for local execution: {name}")

    progress_callback = None
    if enable_progress_notify and supports_tool_progress(name):
        async def progress_callback(progress: float, total: float | None, message: str | None) -> None:
            await emit_tool_progress(
                tool_name=name,
                progress=progress,
                total=total,
                message=message,
                stream_callback=stream_callback
            )

    return await session.call_tool(
        name,
        arguments,
        progress_callback=progress_callback
    )


if __name__ == '__main__':
    pass
