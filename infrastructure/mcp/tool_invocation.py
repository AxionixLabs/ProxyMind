# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.tools.context import (
    NESTED_TOOL_DISPATCH_META_KEY,
    TURN_INTERRUPT_META_KEY,
)
from agent.application.tools.catalog import has_tool
from agent.application.turns.context import ToolInvocation
from agent.domain.tool_policy import supports_progress_notifications
from agent.ports import McpSessionPort
from mcp.types import CallToolResult
from observability import observe


def _tool_progress_text(
    tool_name: str,
    progress: float,
    total: float | None,
    message: str | None,
) -> str:
    """返回一次工具进度通知的稳定文本。"""
    text = str(message or "").strip()
    if text:
        return text
    if total is not None:
        return f"{tool_name} progress={progress}/{total}"
    return f"{tool_name} progress={progress}"


async def _emit_tool_progress(
    *,
    tool_name: str,
    progress: float,
    total: float | None,
    message: str | None,
    stream_callback: typing.Callable[[str], typing.Awaitable[None]] | None,
) -> None:
    """把工具进度投递给当前展示流，无展示流时只记录观测事实。"""
    text = _tool_progress_text(tool_name, progress, total, message)
    if stream_callback is not None:
        await stream_callback(text)
        return None

    observe(
        "tool.progress",
        tool=tool_name,
        progress=progress,
        total=total,
        message_chars=len(text),
    )


def is_hosted_tool(
    tools: list[dict[str, typing.Any]],
    name: str,
    meta: dict[str, typing.Any] | None = None,
) -> bool:
    """判断工具是否由服务端注入，而非本地 MCP 注册。"""
    if has_tool(tools, name):
        return False
    return isinstance(meta, dict) and bool(meta)


async def execute_tool(
    session: McpSessionPort,
    *,
    tools: list[dict[str, typing.Any]],
    invocation: ToolInvocation,
    pref_config: typing.Mapping[str, typing.Any],
    stream_callback: typing.Callable[[str], typing.Awaitable[None]] | None = None,
    enable_progress_notify: bool = False,
) -> CallToolResult:
    """统一工具执行入口。"""
    if not has_tool(tools, invocation.name):
        if is_hosted_tool(tools, invocation.name, meta=invocation.meta):
            raise RuntimeError(
                "Hosted tool is not configured for local execution: "
                f"{invocation.name}"
            )
        raise RuntimeError(
            f"tool is unavailable in this turn: {invocation.name}"
        )

    progress_callback = None

    if enable_progress_notify and supports_progress_notifications(invocation.name):
        async def progress_callback(progress: float, total: float | None, message: str | None) -> None:
            await _emit_tool_progress(
                tool_name=invocation.name,
                progress=progress,
                total=total,
                message=message,
                stream_callback=stream_callback,
            )

    runtime_meta = (
        invocation.meta
        if any(
            key in (invocation.meta or {})
            for key in (
                NESTED_TOOL_DISPATCH_META_KEY,
                TURN_INTERRUPT_META_KEY,
            )
        )
        else None
    )

    return await session.call_tool(
        invocation.name,
        invocation.arguments,
        progress_callback=progress_callback,
        meta=runtime_meta,
        call_id=invocation.call_id,
        turn_context=invocation.turn,
        pref_config=pref_config,
    )


if __name__ == '__main__':
    pass
