# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.utils import get_cwidth
from agent.ports.presentation import ApplicationView
from frontends.terminal.mcp_status import (
    McpStatusDetail,
    McpStatusView,
    external_mcp_status_view,
    render_mcp_status_block,
)
from agent.ports.presentation import (
    TextSpan,
    TextStyle,
)
from ..core.models import (
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from infrastructure.mcp.settings import normalize_mcp_servers
from ..core.styles import (
    ACCENT_STYLE,
    BODY_STYLE,
    BRIGHT_STYLE,
    COMMAND_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    fragment_block,
    interrupted_status_block
)
from ..core.runtime import TuiRuntime, require_tui_runtime

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost

McpAction = typing.Literal[
    "start",
    "force",
    "stop",
    "restart",
    "status"
]

MCP_MENU_ACTIONS: tuple[tuple[McpAction, str, str], ...] = (
    ("start", "start", "Start configured external MCP services with enabled=true; keep already running services connected."),
    ("force", "force", "Temporarily start all configured external MCP services for this turn, including enabled=false. Does not modify the config file."),
    ("stop", "stop", "Disconnect all external MCP services. HTTP/SSE services are disconnected; stdio services close their child processes when released."),
    ("restart", "restart", "Disconnect external MCP services, reload the config, and start services with enabled=true."),
    ("status", "status", "View status without starting or stopping services."),
)

MCP_DEFAULT_TERMINAL_WIDTH = 120
MCP_ENABLED_STATUS_STYLE = TextStyle(foreground="#5FD7AF", dim=True)
MCP_DISABLED_STATUS_STYLE = TextStyle(foreground="#FF6B6B", dim=True)


def _present(
    mind: "TuiApplicationHost",
    renderable: typing.Any = None,
    *,
    view_type: str = "tui.mcp"
) -> None:
    """发送一项外部 MCP 展示。"""
    mind.frontend.application.emit(ApplicationView(
        type=view_type,
        renderable=renderable,
    ))


def _present_external_mcp_result(
    mind: "TuiApplicationHost",
    view: McpStatusView
) -> bool:
    """提交一项外部 MCP 最终状态。"""
    block = render_mcp_status_block(
        view,
        terminal_width=_mcp_terminal_width(mind),
    )
    if not block.plain_text:
        return False

    _present(mind, block, view_type="tui.external_mcp.status")
    _present(mind, view_type="tui.gap")
    return True


def parse_mcp_command(value: str) -> tuple[bool, McpAction | None]:
    """解析外部 MCP 命令及其可选动作。"""
    parts = str(value or "").strip().casefold().split()

    if not parts or parts[0] != "/mcp":
        return False, None
    if len(parts) == 1:
        return True, None
    if len(parts) != 2:
        return False, None

    action = parts[1]
    if action == "start":
        return True, "start"
    if action == "force":
        return True, "force"
    if action == "stop":
        return True, "stop"
    if action == "restart":
        return True, "restart"
    if action == "status":
        return True, "status"

    return False, None


def summarize_external_runtime(
    mind: "TuiApplicationHost",
) -> dict[str, typing.Any]:
    """汇总当前外部 MCP 配置与已连接工具状态。"""
    config_error: str = ""

    try:
        config     = mind.settings.config.load()
        configured = normalize_mcp_servers(config.get("mcp_servers"))
    except (OSError, TypeError, ValueError) as error:
        configured   = []
        config_error = str(error)

    runtime = mind.execution.external_mcp.current
    tool_groups = [
        {
            "server": group.server,
            "transport": group.transport,
            "auth": group.auth,
            "tools": list(group.tools),
            "discovered": group.discovered,
            "exposed": group.exposed,
            "filtered": group.filtered,
        }
        for group in runtime.tool_groups
    ] if runtime is not None else []

    return {
        "started"        : runtime.started if runtime is not None else False,
        "configured"     : configured,
        "config_error"   : config_error,
        "tool_groups"    : tool_groups,
        "tool_count"     : sum(int(item["exposed"]) for item in tool_groups),
        "filtered_count" : sum(int(item["filtered"]) for item in tool_groups),
    }


def _display_tool_name(name: typing.Any, server: str) -> str:
    """移除外部工具名称中的服务前缀。"""
    value  = str(name or "").strip()
    prefix = f"mcp__{server}__"
    return value[len(prefix):] if value.startswith(prefix) else value


def _filtered_count(value: typing.Any) -> int:
    """把过滤工具数量规范化为非负整数。"""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _mcp_terminal_width(mind: "TuiApplicationHost") -> int:
    """返回 MCP 状态块使用的有效终端宽度。"""
    width = mind.frontend.application.viewport.width

    if isinstance(width, int) and width > 0:
        return width

    return MCP_DEFAULT_TERMINAL_WIDTH


def _mcp_tool_name_lines(
    names: typing.Iterable[typing.Any],
    *,
    terminal_width: int,
) -> list[str]:
    """把 MCP 工具名按终端宽度转换为带悬挂缩进的文本行。"""
    visible_names = [
        str(name).strip()
        for name in names
        if str(name).strip()
    ]
    if not visible_names:
        return ["    • Tools: (none)"]

    first_prefix = "    • Tools: "
    continuation_prefix = "      "
    width = max(1, int(terminal_width))
    lines: list[str] = []
    current_prefix = first_prefix
    current_names: list[str] = []

    for index, name in enumerate(visible_names):
        suffix = "," if index < len(visible_names) - 1 else ""
        token = f"{name}{suffix}"
        candidate = f"{current_prefix}{' '.join(current_names + [token])}"

        if current_names and get_cwidth(candidate) > width:
            lines.append(f"{current_prefix}{' '.join(current_names)}")
            current_prefix = continuation_prefix
            current_names = []

        current_names.append(token)

    if current_names:
        lines.append(f"{current_prefix}{' '.join(current_names)}")

    return lines


def _mcp_status_rows(
    configured: typing.Any,
    tool_groups: typing.Any,
) -> list[dict[str, typing.Any]]:
    """合并配置服务器与运行时工具分组为状态行。"""
    configured_servers = [
        item for item in configured
        if isinstance(item, dict)
    ] if isinstance(configured, list) else []
    active_groups = [
        item for item in tool_groups
        if isinstance(item, dict)
    ] if isinstance(tool_groups, list) else []
    active_by_key = {
        (
            str(item.get("server") or "server"),
            str(item.get("transport") or "external"),
        ): item
        for item in active_groups
    }

    rows: list[dict[str, typing.Any]] = []

    for server in configured_servers:
        server_name = str(server.get("name") or "server")
        transport = str(server.get("transport") or "streamable_http")
        key       = (server_name, transport)
        group     = active_by_key.pop(key, None)
        names     = group.get("tools") if isinstance(group, dict) else ()
        tools     = sorted(
            _display_tool_name(raw_name, server_name)
            for raw_name in names or ()
            if str(raw_name)
        )
        filtered = group.get("filtered", 0) if isinstance(group, dict) else 0

        rows.append({
            "name": server_name,
            "status": "enabled" if bool(server.get("enabled", True)) else "disabled",
            "auth": str(group.get("auth") or "Unknown") if group else "Unknown",
            "transport": transport,
            "tools": tools,
            "filtered": _filtered_count(filtered),
        })

    for group in active_by_key.values():
        names = group.get("tools") or ()
        group_name = str(group.get("server") or "server")
        rows.append({
            "name": group_name,
            "status": "connected",
            "auth": str(group.get("auth") or "Unknown"),
            "transport": str(group.get("transport") or "external"),
            "tools": sorted(
                _display_tool_name(name, group_name)
                for name in names
                if str(name)
            ),
            "filtered": _filtered_count(group.get("filtered")),
        })

    return sorted(rows, key=lambda row: (str(row["name"]), str(row["transport"])))


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


def render_mcp_action_result(
    mind: "TuiApplicationHost",
    action: McpAction,
    was_started: bool
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
    mind: "TuiApplicationHost",
    action: McpAction,
    error: BaseException
) -> None:
    """展示外部 MCP 操作失败的最终结果。"""
    if action == "stop":
        render_external_mcp_stop_status(mind, error=error)
    else:
        render_external_mcp_start_status(mind, error=error)


def render_mcp_action_cancelled(
    mind: "TuiApplicationHost",
    action: McpAction,
) -> None:
    """展示外部 MCP 操作取消后的最终结果。"""
    if action == "stop" and mind.execution.external_mcp.current is None:
        render_external_mcp_stop_status(mind)
        return None
    render_mcp_action_interrupted(mind, action)


def render_mcp_action_interrupted(
    mind: "TuiApplicationHost",
    action: McpAction,
) -> None:
    """展示外部 MCP 操作被用户中断的状态。"""
    _present(
        mind,
        interrupted_status_block("External MCP", action=action),
        view_type="tui.external_mcp.interrupted",
    )
    _present(mind, view_type="tui.gap")


def render_external_mcp_start_status(
    mind: "TuiApplicationHost",
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
            details=(McpStatusDetail(f"  └ {detail}", "failed"),),
        )
    else:
        runtime  = mind.execution.external_mcp.current
        snapshot = runtime.last_start_snapshot if runtime is not None else {}
        if not isinstance(snapshot, dict) or not snapshot:
            return False
        view = external_mcp_status_view(snapshot, detail_limit=5)

    return _present_external_mcp_result(mind, view)


def render_external_mcp_stop_status(
    mind: "TuiApplicationHost",
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
            details=(McpStatusDetail(f"  └ {detail}", "failed"),),
        )
    else:
        summary = (
            "External MCP already stopped"
            if already_stopped
            else "External MCP stopped"
        )
        view = McpStatusView(summary=summary, level="ready", done=True)

    _present_external_mcp_result(mind, view)


def render_mcp_status(
    mind: "TuiApplicationHost",
    *,
    command: str | None = "/mcp status"
) -> None:
    """展示外部 MCP 服务状态。"""
    summary     = summarize_external_runtime(mind)
    configured  = summary["configured"]
    tool_groups = summary["tool_groups"]
    terminal_width = _mcp_terminal_width(mind)
    display_command = command or "/mcp"

    parts = [
        TextSpan(display_command, COMMAND_STYLE),
        TextSpan("\n\n"),
        TextSpan("🔌  MCP Tools", BRIGHT_STYLE),
        TextSpan("\n\n"),
    ]

    if summary["config_error"]:
        parts.append(TextSpan(
            f"  ■ {summary['config_error']}",
            FAILURE_STYLE,
        ))
    else:
        rows = _mcp_status_rows(configured, tool_groups)

        if not rows:
            parts.append(TextSpan(
                "  • No MCP servers configured.",
                MUTED_STYLE,
            ))
        else:
            if not any(row["tools"] for row in rows):
                parts.extend([
                    TextSpan("  • No MCP tools available.", MUTED_STYLE),
                    TextSpan("\n\n"),
                ])

            for index, row in enumerate(rows):
                if index:
                    parts.append(TextSpan("\n\n"))

                parts.extend([
                    TextSpan("  • ", ACCENT_STYLE),
                    TextSpan(row["name"], BODY_STYLE),
                ])

                status_style = (
                    MCP_DISABLED_STATUS_STYLE
                    if row["status"] == "disabled"
                    else MCP_ENABLED_STATUS_STYLE
                )

                parts.extend([
                    TextSpan("\n    • Status: ", BODY_STYLE),
                    TextSpan(row["status"], status_style),
                    TextSpan("\n    • Auth: ", BODY_STYLE),
                    TextSpan(row["auth"], MUTED_STYLE),
                    TextSpan("\n    • Transport: ", BODY_STYLE),
                    TextSpan(row["transport"], MUTED_STYLE),
                ])

                for line_index, tool_line in enumerate(_mcp_tool_name_lines(
                    row["tools"],
                    terminal_width=terminal_width,
                )):
                    parts.append(TextSpan("\n", BODY_STYLE))
                    if line_index == 0:
                        tools_prefix = "    • Tools: "
                        parts.extend([
                            TextSpan(tools_prefix, BODY_STYLE),
                            TextSpan(
                                tool_line[len(tools_prefix):],
                                MUTED_STYLE,
                            ),
                        ])
                    else:
                        parts.append(TextSpan(tool_line, MUTED_STYLE))

                if row["filtered"] > 0:
                    parts.extend([
                        TextSpan("\n    • Filtered: ", BODY_STYLE),
                        TextSpan(str(row["filtered"]), MUTED_STYLE),
                    ])

    block = fragment_block(*parts)
    _present(mind, block)
    _present(mind, view_type="tui.gap")


async def _begin_external_mcp_restart_activity(
    mind: "TuiApplicationHost",
) -> None:
    """在断开旧连接前启动外部 MCP 重启活动状态。"""
    if not mind.activity.enabled:
        return None
    runtime = require_tui_runtime(mind.frontend.runtime)
    await runtime.begin_external_mcp_status(
        lambda: {
            "summary": "External MCP restarting",
            "done": False,
            "items": [],
        },
    )


async def choose_mcp_action(
    runtime: "TuiRuntime",
    mind: "TuiApplicationHost",
) -> McpAction | None:
    """在主 TUI 中选择外部 MCP 操作。"""
    summary = summarize_external_runtime(mind)
    actions = list(MCP_MENU_ACTIONS)

    config_error = str(summary.get("config_error") or "")

    return await runtime.select_menu(MenuRequest(
        title="External MCP",
        view_id="mcp:root",
        status="Manage configured external MCP services.",
        body=(config_error,) if config_error else (),
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(
            MenuOption(value=action, label=label, detail=detail)
            for action, label, detail in actions
        ),
        selected=default_mcp_action_index(summary, actions),
    ))


async def finish_mcp_activity(
    mind: "TuiApplicationHost",
    action: McpAction,
) -> None:
    """结束外部 MCP 操作对应的活动状态。"""
    if action == "stop":
        runtime = require_tui_runtime(mind.frontend.runtime)
        await runtime.end_activity_status(
            "operation",
            settle=False,
        )
        return None

    await mind.activity.stop("external_mcp", settle=False)


async def run_mcp_action(
    mind: "TuiApplicationHost",
    action: McpAction | None,
) -> bool:
    """执行外部 MCP 动作并返回操作前是否已经启动。"""
    if action is None:
        return False

    if action == "status":
        return False

    external_runtime = mind.execution.external_mcp.current
    was_started = external_runtime.started if external_runtime is not None else False

    if action == "stop":
        runtime = require_tui_runtime(mind.frontend.runtime)
        if mind.activity.enabled:
            await runtime.begin_operation_status(
                lambda: {"summary": "External MCP stopping"},
            )
        await mind.execution.external_mcp.close()
    elif action == "force":
        runtime = mind.execution.external_mcp.current
        if runtime is not None and runtime.started:
            await _begin_external_mcp_restart_activity(mind)
            await mind.execution.external_mcp.restart(
                include_disabled=True,
                defer_activity_stop=True,
            )
        else:
            await mind.execution.external_mcp.start(
                include_disabled=True,
                defer_activity_stop=True,
            )
    elif action == "start":
        await mind.execution.external_mcp.start(defer_activity_stop=True)
    else:
        await _begin_external_mcp_restart_activity(mind)
        await mind.execution.external_mcp.restart(defer_activity_stop=True)

    return was_started


if __name__ == '__main__':
    pass
