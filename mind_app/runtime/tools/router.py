# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.mcp.contracts import McpSessionLike
from mind_app.mcp.tool_store import has_tool
from mind_app.runtime.execution import ToolInvocation
from mcp.types import CallToolResult
from .notify import (
    emit_tool_progress,
    supports_tool_progress
)

def is_hosted_tool(
    tools: list[dict[str, typing.Any]],
    name: str,
    meta: typing.Optional[dict[str, typing.Any]] = None
) -> bool:
    """判断工具是否由服务端注入，而非本地 MCP 注册。"""
    if has_tool(tools, name):
        return False
    return isinstance(meta, dict) and bool(meta)


async def execute_tool(
    session: McpSessionLike,
    *,
    tools: list[dict[str, typing.Any]],
    invocation: ToolInvocation,
    pref_config: typing.Mapping[str, typing.Any],
    stream_callback: typing.Optional[typing.Callable[[str], typing.Awaitable[None]]] = None,
    enable_progress_notify: bool = False
) -> CallToolResult:
    """统一工具执行入口。"""
    if is_hosted_tool(tools, invocation.name, meta=invocation.meta):
        raise RuntimeError(
            f"Hosted tool is not configured for local execution: {invocation.name}"
        )

    progress_callback = None

    if enable_progress_notify and supports_tool_progress(invocation.name):
        async def progress_callback(progress: float, total: float | None, message: str | None) -> None:
            await emit_tool_progress(
                tool_name=invocation.name,
                progress=progress,
                total=total,
                message=message,
                stream_callback=stream_callback
            )

    return await session.call_tool(
        invocation.name,
        invocation.arguments,
        progress_callback=progress_callback,
        execution=invocation.execution,
        call_id=invocation.call_id,
        turn_context=invocation.turn,
        pref_config=pref_config,
    )


if __name__ == '__main__':
    pass
