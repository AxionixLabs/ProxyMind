# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from engine.tinker import MindError
from mind_core.design import Design
from mind_core.terminal_input import clear_pending_input
from .repl_commands import (
    link_helix_runtime,
    open_helix_home,
    unlink_helix_runtime
)

HelixAction = typing.Literal["link", "unlink", "home", "stop"]

HELIX_MENU_STYLE = Style.from_dict({
    "helix.kicker"       : "bold #5FD7AF",
    "helix.title"        : "bold #E6F6FF",
    "helix.help"         : "#6F7B88",
    "helix.status"       : "#8BBFD1",
    "helix.index"        : "bold #7A8794",
    "helix.index.active" : "bold #101820 bg:#5FD7AF",
    "helix.action"       : "bold #F4F7FA",
    "helix.detail"       : "#7F8C9A",
    "helix.active"       : "#F4F7FA bg:#1D332E"
})

HELIX_MENU_ACTIONS: tuple[tuple[HelixAction, str, str], ...] = (
    ("link", "link", "接入本地 Helix 服务，并挂载 Helix MCP 工具。"),
    ("unlink", "unlink", "从当前会话移除 Helix MCP，不停止服务。"),
    ("home", "home", "启动或复用 Helix 服务，并打开首页。"),
    ("stop", "stop", "停止 Helix 服务。")
)


async def choose_helix_action(mind: typing.Any) -> HelixAction | None:
    """显示 Helix 服务操作菜单，并返回选择的动作。"""
    selected = [_default_helix_action_index(mind)]
    bindings = KeyBindings()

    def move(step: int) -> None:
        selected[0] = min(len(HELIX_MENU_ACTIONS) - 1, max(0, selected[0] + step))

    def choose(index: int, event: typing.Any) -> None:
        if 0 <= index < len(HELIX_MENU_ACTIONS):
            event.app.exit(result=HELIX_MENU_ACTIONS[index][0])

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

    for number in range(1, len(HELIX_MENU_ACTIONS) + 1):
        @bindings.add(str(number))
        def _(event, selected_number=number) -> None:
            choose(selected_number - 1, event)

    @bindings.add("c-c")
    @bindings.add("q")
    def _(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(
        lambda: render_helix_menu(mind, selected[0]),
        focusable=True
    )

    app: Application[HelixAction | None] = Application(
        layout=Layout(
            Window(
                content=control,
                height=len(HELIX_MENU_ACTIONS) + 4,
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=HELIX_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    return await app.run_async(pre_run=lambda: clear_pending_input(app.input))


def render_helix_menu(mind: typing.Any, selected: int) -> StyleAndTextTuples:
    """生成 Helix 服务操作菜单内容。"""
    action_width = max(len(label) for _, label, _ in HELIX_MENU_ACTIONS)
    lines: StyleAndTextTuples = [
        ("class:helix.kicker", "Helix"),
        ("class:helix.title", " Service"),
        ("", "\n"),
        ("class:helix.status", _helix_status_line(mind)),
        ("", "\n"),
        ("class:helix.help", "↑/↓ select · Enter apply · q close"),
        ("", "\n")
    ]

    for index, (_, label, detail) in enumerate(HELIX_MENU_ACTIONS):
        active = index == selected

        prefix_style = "class:helix.index.active" if active else "class:helix.index"
        row_style    = "class:helix.active" if active else ""
        marker       = ">" if active else " "

        lines.extend([
            (prefix_style, f"{marker} {index + 1} "),
            (row_style or "class:helix.action", f" {label.ljust(action_width)}"),
            ("class:helix.detail", f"  {detail}"),
            ("", "\n")
        ])

    return lines


async def run_helix_action(mind: typing.Any, action: HelixAction | None) -> bool:
    """执行 Helix 服务菜单动作，并返回是否需要刷新工作区状态。"""
    if action is None:
        Design.console.print()
        return False

    if action == "link":
        await link_helix_runtime(mind)
        return True

    if action == "unlink":
        unlink_helix_runtime(mind)
        return True

    if action == "home":
        await open_helix_home(mind)
        return True

    await stop_helix_runtime(mind)
    return True


async def stop_helix_runtime(mind: typing.Any) -> None:
    """停止 Helix 服务并打印结果。"""
    Design.console.print("[bold #AFC7D8]Helix[/] [dim #7F8C9A]· stop[/]")
    try:
        await mind.stop_service_runtime()
    except MindError as error:
        Design.console.print(f"[bold #FF5F5F]Helix stop failed: {error}[/]")
        Design.console.print()
        return None
    Design.console.print()


def _helix_status_line(mind: typing.Any) -> str:
    """返回 Helix 当前会话挂载状态。"""
    linked = bool(mind.is_service_mcp_linked())
    return f"linked={str(linked).lower()}"


def _default_helix_action_index(mind: typing.Any) -> int:
    """根据当前状态选择 Helix 菜单默认高亮项。"""
    return 1 if bool(mind.is_service_mcp_linked()) else 0


if __name__ == '__main__':
    pass
