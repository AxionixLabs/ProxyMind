# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections import defaultdict
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from mind_app.frontend import ApplicationView
from mind_app.mcp.config import load_mcp_servers_file
from mind_core.terminal_input import clear_pending_input

McpAction = typing.Literal["start", "force", "stop", "restart", "status"]

MCP_MENU_STYLE = Style.from_dict({
    "mcp.title"        : "bold #E6F6FF",
    "mcp.help"         : "#69727D",
    "mcp.status"       : "#87919D",
    "mcp.index"        : "bold #8A949F",
    "mcp.index.active" : "bold #101820 bg:#87D7FF",
    "mcp.action"       : "bold #F4F7FA",
    "mcp.detail"       : "#7F8C9A",
    "mcp.active"       : "#F4F7FA bg:#1D2F3A"
})

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
    view_type: str = "repl.mcp",
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


async def choose_mcp_action(mind: typing.Any) -> McpAction | None:
    """显示外部 MCP 操作菜单，并返回选择的动作。"""
    summary  = summarize_external_runtime(mind)
    actions  = selectable_mcp_actions(summary)
    selected = [default_mcp_action_index(summary, actions)]
    bindings = KeyBindings()

    def move(step: int) -> None:
        selected[0] = min(len(actions) - 1, max(0, selected[0] + step))

    def choose(index: int, event: typing.Any) -> None:
        if 0 <= index < len(actions):
            event.app.exit(result=actions[index][0])

    @bindings.add("enter")
    def _(event) -> None:
        choose(selected[0], event)

    @bindings.add("down")
    @bindings.add("c-n")
    def _(event) -> None:
        move(1)
        event.app.invalidate()

    @bindings.add("up")
    @bindings.add("c-p")
    def _(event) -> None:
        move(-1)
        event.app.invalidate()

    for number in range(1, min(9, len(actions)) + 1):
        @bindings.add(str(number))
        def _(event, selected_number=number) -> None:
            choose(selected_number - 1, event)

    @bindings.add("c-c")
    @bindings.add("q")
    def _(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(
        lambda: render_mcp_menu(summary, actions, selected[0]),
        focusable=True
    )

    app: Application[McpAction | None] = Application(
        layout=Layout(
            Window(
                content=control,
                height=len(actions) + 4,
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=MCP_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    return await app.run_async(pre_run=lambda: clear_pending_input(app.input))


def render_mcp_menu(
    summary: dict[str, typing.Any],
    actions: list[tuple[McpAction, str, str]],
    selected: int
) -> StyleAndTextTuples:
    """生成外部 MCP 操作菜单内容。"""
    action_width = max((len(label) for _, label, _ in actions), default=1)
    lines: StyleAndTextTuples = [
        ("class:mcp.title", "External MCP"),
        ("", "\n"),
        ("class:mcp.status", external_status_line(summary)),
        ("", "\n"),
        ("class:mcp.help", "↑/↓ select · Enter apply · q close"),
        ("", "\n")
    ]

    for index, (_, label, detail) in enumerate(actions):
        active = index == selected

        prefix_style = "class:mcp.index.active" if active else "class:mcp.index"
        row_style    = "class:mcp.active" if active else ""
        marker       = ">" if active else " "

        lines.extend([
            (prefix_style, f"{marker} {index + 1} "),
            (row_style or "class:mcp.action", f" {label.ljust(action_width)}"),
            ("class:mcp.detail", f"  {detail}"),
            ("", "\n")
        ])

    return lines


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
        _present(mind, view_type="repl.gap")
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
        f"[bold #AFC7D8]External MCP[/] "
        f"[dim #7F8C9A]· started={str(summary['started']).lower()} "
        f"configured={len(configured)} tools={summary['tool_count']}[/]"
    )

    if configured:
        _present(mind, "[bold #F4F7FA]Configured servers[/]")
        for server in configured:
            name      = str(server.get("name") or "server")
            transport = str(server.get("transport") or "streamable_http")
            enabled   = bool(server.get("enabled", True))
            state     = "enabled" if enabled else "disabled"

            _present(
                mind,
                f"[bold #AFC7D8]  •[/] [#DDE7EF]{name}[/] "
                f"[dim #7F8C9A]({transport} · {state})[/]"
            )
    else:
        _present(mind, "[bold #7F8C9A]No external MCP servers configured.[/]")

    if tool_groups:
        _present(mind, "[bold #F4F7FA]Connected tools[/]")
        for group in tool_groups:
            names = group["tools"]
            _present(
                mind,
                f"[bold #AFC7D8]  •[/] [#DDE7EF]{group['server']}[/] "
                f"[dim #7F8C9A]({group['transport']} · {len(names)} tools)[/]"
            )
    else:
        _present(mind, "[bold #7F8C9A]No external MCP tools connected.[/]")

    _present(mind, view_type="repl.gap")


if __name__ == '__main__':
    pass
