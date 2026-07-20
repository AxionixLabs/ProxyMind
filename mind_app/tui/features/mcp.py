# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections import defaultdict
from mind_app.frontend import ApplicationView
from mind_app.presentation.models import TextSpan
from ..core.models import (
    MenuOption,
    MenuRequest
)
from mind_app.mcp.config import load_mcp_servers_file
from ..core.styles import (
    ACCENT_STYLE,
    BODY_STYLE,
    BRIGHT_STYLE,
    MUTED_STYLE,
    fragment_block,
    text_block
)

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime

McpAction = typing.Literal["start", "force", "stop", "restart", "status"]

MCP_MENU_ACTIONS: tuple[tuple[McpAction, str, str], ...] = (
    ("start", "start", "启动 enabled=true 的外接 MCP 服务；已启动则保持当前连接。"),
    ("force", "force", "本轮临时启动所有已配置的外接 MCP 服务，包括 enabled=false 的。不会修改配置文件。"),
    ("stop", "stop", "断开当前所有外接 MCP 连接。HTTP/SSE 只是断开连接；stdio 类型会随连接释放关闭对应子进程。"),
    ("restart", "restart", "先断开当前外接 MCP，再重新读取配置并启动 enabled=true 的服务。"),
    ("status", "status", "查看状态，不启动、不停止。")
)


def _present(
    mind: typing.Any,
    renderable: typing.Any = None,
    *,
    view_type: str = "tui.mcp",
) -> None:
    """发送一项外部 MCP 展示。"""
    mind.frontend.application.emit(ApplicationView(
        type=view_type,
        renderable=renderable,
    ))


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


def selectable_mcp_actions(summary: dict[str, typing.Any]) -> list[tuple[McpAction, str, str]]:
    """根据当前配置生成外部 MCP 操作列表。"""
    configured = summary.get("configured")
    has_config = bool(configured)

    if has_config:
        return list(MCP_MENU_ACTIONS)

    if bool(summary.get("started")):
        return [
            ("stop", "stop", "断开当前所有外接 MCP 连接。HTTP/SSE 只是断开连接；stdio 类型会随连接释放关闭对应子进程。"),
            ("status", "status", "查看状态，不启动、不停止。")
        ]

    return [
        ("status", "status", "查看状态，不启动、不停止。")
    ]


def default_mcp_action_index(
    summary: dict[str, typing.Any],
    actions: list[tuple[McpAction, str, str]]
) -> int:
    """根据当前状态选择菜单默认高亮项。"""
    configured   = summary.get("configured")
    servers      = configured if isinstance(configured, list) else []
    has_disabled = any(not bool(server.get("enabled", True)) for server in servers)

    preferred: McpAction = "status"
    if bool(summary.get("tool_count")):
        preferred = "stop"
    elif bool(summary.get("started")) and has_disabled:
        preferred = "force"
    elif not bool(summary.get("started")) and servers:
        preferred = "start"
    elif bool(summary.get("started")):
        preferred = "restart"

    for index, (action, _, _) in enumerate(actions):
        if action == preferred:
            return index

    return 0


async def choose_mcp_action(
    runtime: "TuiRuntime",
    mind: typing.Any,
) -> McpAction | None:
    """在主 TUI 中选择外部 MCP 操作。"""
    summary  = summarize_external_runtime(mind)
    actions  = selectable_mcp_actions(summary)
    return await runtime.select_menu(MenuRequest(
        title="External MCP",
        status=external_status_line(summary),
        options=tuple(
            MenuOption(value=action, label=label, detail=detail)
            for action, label, detail in actions
        ),
        selected=default_mcp_action_index(summary, actions),
    ))


def external_status_line(summary: dict[str, typing.Any]) -> str:
    """返回外部 MCP 状态摘要文本。"""
    configured = summary.get("configured")
    return (
        f"started={str(bool(summary.get('started'))).lower()} "
        f"· configured={len(configured) if isinstance(configured, list) else 0} "
        f"· tools={int(summary.get('tool_count') or 0)}"
    )


async def run_mcp_action(mind: typing.Any, action: McpAction | None) -> None:
    """执行外部 MCP 菜单动作。"""
    if action is None:
        _present(mind, view_type="tui.gap")
        return None

    if action == "status":
        render_mcp_status(mind)
        return None

    if action == "stop":
        await mind.stop_external_mcp_runtime()
        render_mcp_status(mind)
        return None

    if action == "force":
        await mind.restart_external_mcp_runtime(include_disabled=True)
        render_mcp_status(mind)
        return None

    if action == "start":
        await mind.start_external_mcp_runtime()
        render_mcp_status(mind)
        return None

    await mind.restart_external_mcp_runtime()
    render_mcp_status(mind)
    return None


def render_mcp_status(mind: typing.Any) -> None:
    """展示外部 MCP 服务状态。"""
    summary     = summarize_external_runtime(mind)
    configured  = summary["configured"]
    tool_groups = summary["tool_groups"]

    _present(
        mind,
        fragment_block(
            TextSpan("External MCP ", ACCENT_STYLE),
            TextSpan(
                f"· started={str(summary['started']).lower()} "
                f"configured={len(configured)} tools={summary['tool_count']}",
                MUTED_STYLE,
            ),
        )
    )

    if configured:
        _present(mind, text_block("Configured servers", BRIGHT_STYLE))
        for server in configured:
            name      = str(server.get("name") or "server")
            transport = str(server.get("transport") or "streamable_http")
            enabled   = bool(server.get("enabled", True))
            state     = "enabled" if enabled else "disabled"

            _present(
                mind,
                fragment_block(
                    TextSpan("  • ", ACCENT_STYLE),
                    TextSpan(f"{name} ", BODY_STYLE),
                    TextSpan(f"({transport} · {state})", MUTED_STYLE),
                )
            )
    else:
        _present(
            mind,
            text_block("No external MCP servers configured.", MUTED_STYLE),
        )

    if tool_groups:
        _present(mind, text_block("Connected tools", BRIGHT_STYLE))
        for group in tool_groups:
            names = group["tools"]
            _present(
                mind,
                fragment_block(
                    TextSpan("  • ", ACCENT_STYLE),
                    TextSpan(f"{group['server']} ", BODY_STYLE),
                    TextSpan(
                        f"({group['transport']} · {len(names)} tools)",
                        MUTED_STYLE,
                    ),
                )
            )
    else:
        _present(
            mind,
            text_block("No external MCP tools connected.", MUTED_STYLE),
        )

    _present(mind, view_type="tui.gap")


if __name__ == '__main__':
    pass
