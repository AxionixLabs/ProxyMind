# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mcp import types as mcp_types


def client_tool_result(
    *,
    tool: str,
    ok: bool,
    text: str,
    data: dict[str, typing.Any] | None = None,
    args: dict[str, typing.Any] | None = None,
    attachments: list[typing.Any] | None = None,
    logs: list[typing.Any] | None = None,
) -> mcp_types.CallToolResult:
    """构造客户端工具的标准 CallToolResult。"""
    result_text = f"tool={tool} source=client ok={ok} {text}".strip()

    structured: dict[str, typing.Any] | None = {
        "ok"          : bool(ok),
        "tool"        : tool,
        "source"      : "client",
        "args"        : dict(args or {}),
        "text"        : result_text,
        "attachments" : list(attachments or []),
        "data"        : dict(data or {})
    }

    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=result_text)],
        structuredContent=structured,
        isError=not bool(ok),
        _meta={"logs": list(logs or [])}
    )


if __name__ == '__main__':
    pass
