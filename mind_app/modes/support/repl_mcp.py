# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections import defaultdict
from mind_app.mcp.config import load_mcp_servers_file
from mind_core.design import Design


def summarize_external_runtime(mind: typing.Any) -> dict[str, typing.Any]:
    """汇总当前外部 MCP 配置与已连接工具状态。"""
    configured = load_mcp_servers_file(getattr(mind, "src_opera_place", ""))

    runtime = getattr(mind, "external_mcp", None)
    group   = getattr(runtime, "group", None) if runtime is not None else None
    tools   = getattr(group, "tools", {}) if group is not None else {}

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)

    for name, tool in dict(tools or {}).items():

        meta      = dict(getattr(tool, "meta", None) or {})
        server    = str(meta.get("server") or "external").strip() or "external"
        transport = str(meta.get("transport") or "external").strip() or "external"

        grouped[(server, transport)].append(str(name))

    tool_groups = [
        {
            "server"    : server,
            "transport" : transport,
            "tools"     : sorted(names)
        }
        for (server, transport), names in grouped.items()
    ]
    tool_groups.sort(key=lambda item: (str(item["server"]), str(item["transport"])))

    return {
        "started"     : bool(getattr(runtime, "started", False)) if runtime is not None else False,
        "configured"  : configured,
        "tool_groups" : tool_groups,
        "tool_count"  : sum(len(item["tools"]) for item in tool_groups)
    }


def render_mcp_status(mind: typing.Any) -> None:
    """打印外部 MCP runtime 状态。"""
    summary     = summarize_external_runtime(mind)
    configured  = summary["configured"]
    tool_groups = summary["tool_groups"]

    Design.console.print(
        f"[bold #AFC7D8]External MCP[/] "
        f"[dim #7F8C9A]· started={str(summary['started']).lower()} "
        f"configured={len(configured)} tools={summary['tool_count']}[/]"
    )

    if configured:
        Design.console.print("[bold #F4F7FA]Configured servers[/]")
        for server in configured:
            name      = str(server.get("name") or "server")
            transport = str(server.get("transport") or "streamable_http")
            enabled   = bool(server.get("enabled", True))
            state     = "enabled" if enabled else "disabled"

            Design.console.print(
                f"[bold #AFC7D8]  •[/] [#DDE7EF]{name}[/] "
                f"[dim #7F8C9A]({transport} · {state})[/]"
            )
    else:
        Design.console.print("[bold #7F8C9A]No external MCP servers configured.[/]")

    if tool_groups:
        Design.console.print("[bold #F4F7FA]Connected tools[/]")
        for group in tool_groups:
            names = group["tools"]
            Design.console.print(
                f"[bold #AFC7D8]  •[/] [#DDE7EF]{group['server']}[/] "
                f"[dim #7F8C9A]({group['transport']} · {len(names)} tools)[/]"
            )
    else:
        Design.console.print("[bold #7F8C9A]No external MCP tools connected.[/]")

    Design.console.print()


if __name__ == '__main__':
    pass
