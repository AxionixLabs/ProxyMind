# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections import defaultdict
from mind_app.frontend import ApplicationView
from mind_app.presentation.mcp_status import render_mcp_status_block
from mind_app.presentation.models import TextSpan
from mind_core.mcp_status import (
    McpStatusDetail,
    McpStatusView,
    external_mcp_status_view
)
from ..core.models import (
    MenuOption,
    MenuRequest
)
from mind_app.mcp.config import normalize_mcp_servers
from ..core.styles import (
    ACCENT_STYLE,
    BODY_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    command_result_block,
    failure_text_block,
    fragment_block,
    interrupted_status_block
)
from ..core.runtime import TuiRuntime, require_tui_runtime

McpAction = typing.Literal[
    "start",
    "force",
    "stop",
    "restart",
    "status"
]

_MCP_ACTIONS: typing.Final[frozenset[str]] = frozenset({
    "start",
    "force",
    "stop",
    "restart",
    "status",
})

MCP_MENU_ACTIONS: tuple[tuple[McpAction, str, str], ...] = (
    ("start", "start", "启动 enabled=true 的外接 MCP 服务；已启动则保持当前连接。"),
    ("force", "force", "本轮临时启动所有已配置的外接 MCP 服务，包括 enabled=false 的。不会修改配置文件。"),
    ("stop", "stop", "断开当前所有外接 MCP 连接。HTTP/SSE 只是断开连接；stdio 类型会随连接释放关闭对应子进程。"),
    ("restart", "restart", "先断开当前外接 MCP，再重新读取配置并启动 enabled=true 的服务。"),
    ("status", "status", "查看状态，不启动、不停止。"),
)


def parse_mcp_command(value: str) -> tuple[bool, McpAction | None]:
    """解析外部 MCP 命令及其可选动作。"""
    parts = str(value or "").strip().casefold().split()

    if not parts or parts[0] != "/mcp":
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) == 2 and parts[1] in _MCP_ACTIONS:
        return True, typing.cast(McpAction, parts[1])

    return False, None


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
    config_error: str = ""

    try:
        config     = mind.config_session.load()
        configured = normalize_mcp_servers(config.get("mcp_servers"))
    except (OSError, TypeError, ValueError) as error:
        configured   = []
        config_error = str(error)

    runtime      = getattr(mind, "external_mcp", None)
    group        = getattr(runtime, "group", None) if runtime is not None else None
    tools        = getattr(group, "tools", {}) if group is not None else {}
    server_stats = getattr(group, "server_stats", {}) if group is not None else {}

    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)

    for name, tool in dict(tools or {}).items():

        meta      = dict(getattr(tool, "meta", None) or {})
        server    = str(meta.get("server") or "external").strip() or "external"
        transport = str(meta.get("transport") or "external").strip() or "external"

        grouped[(server, transport)].append(str(name))

    tool_groups: list[dict[str, typing.Any]] = []
    for stats in dict(server_stats or {}).values():
        server     = str(stats.get("server") or "external")
        transport  = str(stats.get("transport") or "external")
        names      = grouped.pop((server, transport), [])
        exposed    = len(names)
        discovered = max(exposed, int(stats.get("discovered") or 0))

        tool_groups.append({
            "server"     : server,
            "transport"  : transport,
            "tools"      : sorted(names),
            "discovered" : discovered,
            "exposed"    : exposed,
            "filtered"   : max(0, discovered - exposed),
        })

    tool_groups.extend(
        {
            "server"     : server,
            "transport"  : transport,
            "tools"      : sorted(names),
            "discovered" : len(names),
            "exposed"    : len(names),
            "filtered"   : 0,
        }
        for (server, transport), names in grouped.items()
    )
    tool_groups.sort(key=lambda item: (str(item["server"]), str(item["transport"])))

    return {
        "started"        : bool(getattr(runtime, "started", False)) if runtime is not None else False,
        "configured"     : configured,
        "config_error"   : config_error,
        "tool_groups"    : tool_groups,
        "tool_count"     : sum(int(item["exposed"]) for item in tool_groups),
        "filtered_count" : sum(int(item["filtered"]) for item in tool_groups),
    }


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
    actions  = list(MCP_MENU_ACTIONS)

    config_error = str(summary.get("config_error") or "")

    return await runtime.select_menu(MenuRequest(
        title="External MCP",
        status=external_status_line(summary),
        body=(config_error,) if config_error else (),
        options=tuple(
            MenuOption(value=action, label=label, detail=detail)
            for action, label, detail in actions
        ),
        selected=default_mcp_action_index(summary, actions),
    ))


def external_status_line(summary: dict[str, typing.Any]) -> str:
    """返回外部 MCP 状态摘要文本。"""
    if summary.get("config_error"):
        return "config=invalid"

    configured = summary.get("configured")

    return (
        f"started={str(bool(summary.get('started'))).lower()} "
        f"· configured={len(configured) if isinstance(configured, list) else 0} "
        f"· tools={int(summary.get('tool_count') or 0)} "
        f"· filtered={int(summary.get('filtered_count') or 0)}"
    )


async def run_mcp_action(
    mind: typing.Any,
    action: McpAction | None,
) -> bool:
    """执行外部 MCP 动作并返回操作前是否已经启动。"""
    if action is None:
        return False

    if action == "status":
        return False

    external_runtime = getattr(mind, "external_mcp", None)
    was_started      = bool(getattr(external_runtime, "started", False))

    if action == "stop":
        runtime = require_tui_runtime(mind.frontend.runtime)
        if bool(getattr(mind, "animate", True)):
            await runtime.begin_operation_status(
                lambda: {"summary": "External MCP stopping"},
            )
        await mind.stop_external_mcp_runtime()
    elif action == "force":
        runtime = getattr(mind, "external_mcp", None)
        if bool(getattr(runtime, "started", False)):
            await _begin_external_mcp_restart_activity(mind)
            await mind.restart_external_mcp_runtime(
                include_disabled=True,
                defer_activity_stop=True,
            )
        else:
            await mind.start_external_mcp_runtime(
                include_disabled=True,
                defer_activity_stop=True,
            )
    elif action == "start":
        await mind.start_external_mcp_runtime(defer_activity_stop=True)
    else:
        await _begin_external_mcp_restart_activity(mind)
        await mind.restart_external_mcp_runtime(defer_activity_stop=True)

    return was_started


async def _begin_external_mcp_restart_activity(mind: typing.Any) -> None:
    """在断开旧连接前启动外部 MCP 重启活动状态。"""
    if not bool(getattr(mind, "animate", True)):
        return None
    runtime = require_tui_runtime(mind.frontend.runtime)
    await runtime.begin_external_mcp_status(
        lambda: {
            "summary": "External MCP restarting",
            "done": False,
            "items": [],
        },
    )


async def finish_mcp_activity(mind: typing.Any, action: McpAction) -> None:
    """结束外部 MCP 操作对应的活动状态。"""
    if action == "stop":
        runtime = require_tui_runtime(mind.frontend.runtime)
        await runtime.end_activity_status(
            "operation",
            settle=False,
        )
        return None

    await mind.stop_anim("external_mcp", settle=False)


def render_mcp_action_result(
    mind: typing.Any,
    action: McpAction,
    was_started: bool,
) -> None:
    """展示外部 MCP 操作的最终结果。"""
    if action == "stop":
        render_external_mcp_stop_status(
            mind,
            already_stopped=not was_started,
        )
        return None

    if not render_external_mcp_start_status(mind):
        render_mcp_status(mind, command=None)


def render_mcp_action_failure(
    mind: typing.Any,
    action: McpAction,
    error: BaseException
) -> None:
    """展示外部 MCP 操作失败的最终结果。"""
    if action == "stop":
        render_external_mcp_stop_status(mind, error=error)
    else:
        render_external_mcp_start_status(mind, error=error)


def render_mcp_action_cancelled(mind: typing.Any, action: McpAction) -> None:
    """展示外部 MCP 操作取消后的最终结果。"""
    if action == "stop" and getattr(mind, "external_mcp", None) is None:
        render_external_mcp_stop_status(mind)
        return None
    render_mcp_action_interrupted(mind, action)


def render_mcp_action_interrupted(mind: typing.Any, action: McpAction) -> None:
    """展示外部 MCP 操作被用户中断的状态。"""
    _present(
        mind,
        interrupted_status_block("External MCP", action=action),
        view_type="tui.external_mcp.interrupted",
    )
    _present(mind, view_type="tui.gap")


def render_external_mcp_start_status(
    mind: typing.Any,
    *,
    error: BaseException | None = None,
) -> bool:
    """展示最近一次外部 MCP 启动的最终状态。"""
    if error is not None:
        detail = (
            str(getattr(error, "message", error)).strip()
            or type(error).__name__
        )
        view = McpStatusView(
            summary="External MCP failed",
            level="failed",
            done=True,
            details=(McpStatusDetail(f"└ {detail}", "failed"),),
        )
    else:
        runtime  = getattr(mind, "external_mcp", None)
        snapshot = getattr(runtime, "last_start_snapshot", {})
        if not isinstance(snapshot, dict) or not snapshot:
            return False
        view = external_mcp_status_view(snapshot, detail_limit=5)

    return _present_external_mcp_result(mind, view)


def render_external_mcp_stop_status(
    mind: typing.Any,
    *,
    already_stopped: bool = False,
    error: BaseException | None = None,
) -> None:
    """展示外部 MCP 停止后的最终状态。"""
    if error is not None:
        detail = (
            str(getattr(error, "message", error)).strip()
            or type(error).__name__
        )
        view = McpStatusView(
            summary="External MCP stop failed",
            level="failed",
            done=True,
            details=(McpStatusDetail(f"└ {detail}", "failed"),),
        )
    else:
        summary = (
            "External MCP already stopped"
            if already_stopped
            else "External MCP stopped"
        )
        view = McpStatusView(summary=summary, level="ready", done=True)

    _present_external_mcp_result(mind, view)


def _present_external_mcp_result(
    mind: typing.Any,
    view: McpStatusView
) -> bool:
    """提交一项外部 MCP 最终状态。"""
    block = render_mcp_status_block(view)
    if not block.plain_text:
        return False

    _present(mind, block, view_type="tui.external_mcp.status")
    _present(mind, view_type="tui.gap")
    return True


def render_mcp_status(
    mind: typing.Any,
    *,
    command: str | None = "/mcp status"
) -> None:
    """展示外部 MCP 服务状态。"""
    summary     = summarize_external_runtime(mind)
    configured  = summary["configured"]
    tool_groups = summary["tool_groups"]

    if summary["config_error"]:
        block = (
            command_result_block(
                command,
                TextSpan(summary["config_error"], FAILURE_STYLE),
            )
            if command is not None
            else failure_text_block(summary["config_error"])
        )
        _present(mind, block)
        _present(mind, view_type="tui.gap")
        return None

    parts = [
        TextSpan(
            f"started={str(summary['started']).lower()} "
            f"configured={len(configured)} tools={summary['tool_count']} "
            f"filtered={summary['filtered_count']}",
            MUTED_STYLE,
        ),
    ]

    if configured:
        parts.extend([
            TextSpan("\n"),
            TextSpan("Configured servers", BRIGHT_STYLE),
        ])
        for server in configured:
            name      = str(server.get("name") or "server")
            transport = str(server.get("transport") or "streamable_http")
            enabled   = bool(server.get("enabled", True))
            state     = "enabled" if enabled else "disabled"

            parts.extend([
                TextSpan("\n  • ", ACCENT_STYLE),
                TextSpan(f"{name} ", BODY_STYLE),
                TextSpan(f"({transport} · {state})", MUTED_STYLE),
            ])
    else:
        parts.extend([
            TextSpan("\n"),
            TextSpan("No external MCP servers configured.", MUTED_STYLE),
        ])

    if tool_groups:
        parts.extend([
            TextSpan("\n"),
            TextSpan("Connected servers", BRIGHT_STYLE),
        ])
        for group in tool_groups:
            names = group["tools"]
            parts.extend([
                TextSpan("\n  • ", ACCENT_STYLE),
                TextSpan(f"{group['server']} ", BODY_STYLE),
                TextSpan(
                    f"({group['transport']} · "
                    f"discovered={group['discovered']} "
                    f"exposed={len(names)} filtered={group['filtered']})",
                    MUTED_STYLE,
                ),
            ])
    else:
        parts.extend([
            TextSpan("\n"),
            TextSpan("No external MCP servers connected.", MUTED_STYLE),
        ])

    block = (
        command_result_block(command, *parts)
        if command is not None
        else fragment_block(
            TextSpan("External MCP ", ACCENT_STYLE),
            TextSpan("· ", MUTED_STYLE),
            *parts,
        )
    )
    _present(mind, block)
    _present(mind, view_type="tui.gap")


if __name__ == '__main__':
    pass
