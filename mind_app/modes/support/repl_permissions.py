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
from mind_core.design import Design
from mind_core.terminal_input import clear_pending_input
from mind_nova.requests import (
    access_mode_label,
    normalize_access_mode
)

PERMISSION_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("safe", "Approval", "tool execution requires approval"),
    ("full", "Elevated", "tool execution may run without approval")
)

PERMISSIONS_MENU_STYLE = Style.from_dict({
    "permissions.title"        : "bold #DCE6EE",
    "permissions.help"         : "#69727D",
    "permissions.index"        : "bold #8A949F",
    "permissions.index.active" : "bold #F4F7FA bg:#3A4651",
    "permissions.label"        : "bold #F4F7FA",
    "permissions.detail"       : "#7F8C9A",
    "permissions.active"       : "#F4F7FA bg:#26313A"
})


async def choose_permissions_mode(current_mode: typing.Any) -> str | None:
    """显示权限模式菜单，返回选中的 safe/full。"""
    current = normalize_access_mode(current_mode)
    selected = [1 if current == "full" else 0]
    bindings = KeyBindings()

    def move(step: int) -> None:
        selected[0] = min(len(PERMISSION_OPTIONS) - 1, max(0, selected[0] + step))

    def choose(index: int, event) -> None:
        if 0 <= index < len(PERMISSION_OPTIONS):
            event.app.exit(result=PERMISSION_OPTIONS[index][0])

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

    @bindings.add("1")
    def _(event) -> None:
        choose(0, event)

    @bindings.add("2")
    def _(event) -> None:
        choose(1, event)

    @bindings.add("c-c")
    @bindings.add("q")
    def _(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(
        lambda: _render_permissions_menu(selected[0]),
        focusable=True
    )

    app: Application[str | None] = Application(
        layout=Layout(
            Window(
                content=control,
                height=5,
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=PERMISSIONS_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    return await app.run_async(pre_run=lambda: clear_pending_input(app.input))


def _render_permissions_menu(selected: int) -> StyleAndTextTuples:
    """生成权限模式菜单内容。"""
    lines: StyleAndTextTuples = [
        ("class:permissions.title", "Permissions"),
        ("", "\n"),
        ("class:permissions.help", "↑/↓ select · Enter apply · q cancel"),
        ("", "\n")
    ]

    for index, (_, label, detail) in enumerate(PERMISSION_OPTIONS):
        active = index == selected

        prefix_style = "class:permissions.index.active" if active else "class:permissions.index"
        row_style    = "class:permissions.active" if active else ""
        marker       = ">" if active else " "

        lines.extend([
            (prefix_style, f"{marker} {index + 1} "),
            (row_style or "class:permissions.label", f" {label}"),
            ("class:permissions.detail", f" · {detail}"),
            ("", "\n")
        ])

    return lines


def render_permissions_status(access_mode: typing.Any) -> None:
    """打印当前权限模式。"""
    normalized = normalize_access_mode(access_mode)
    label      = access_mode_label(normalized)

    detail = (
        "tool execution requires approval"
        if normalized == "safe"
        else "tool execution may run without approval"
    )
    color = "#D8B26E" if normalized == "full" else "#8FC7EA"

    Design.console.print(
        f"[bold {color}]Permissions[/] "
        f"[bold #F4F7FA]· {label}[/]"
    )
    Design.console.print(f"[dim #7F8C9A]└ {detail}[/]")
    Design.console.print()


if __name__ == '__main__':
    pass
