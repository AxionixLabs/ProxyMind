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
from mind_core.provider_config import DEFAULT_REASONING_EFFORT
from mind_core.terminal_input import clear_pending_input
from .repl_prompt import normalize_reasoning_effort

MODEL_EFFORT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("low", "低推理，优先速度"),
    ("medium", "默认档位，平衡速度与质量"),
    ("high", "高推理，提升复杂任务质量"),
    ("xhigh", "最高推理，适合困难任务"),
)

MODEL_MENU_STYLE = Style.from_dict({
    "model.title"        : "bold #DCE6EE",
    "model.help"         : "#69727D",
    "model.status"       : "#87919D",
    "model.index"        : "bold #8A949F",
    "model.index.active" : "bold #F4F7FA bg:#3A4651",
    "model.value"        : "bold #F4F7FA",
    "model.detail"       : "#7F8C9A",
    "model.active"       : "#F4F7FA bg:#26313A"
})


async def choose_model_effort(current_effort: typing.Any) -> str | None:
    """显示推理强度菜单，并返回选中的档位。"""
    current  = normalize_reasoning_effort(current_effort)
    selected = [_default_effort_index(current)]
    bindings = KeyBindings()

    def move(step: int) -> None:
        selected[0] = min(len(MODEL_EFFORT_OPTIONS) - 1, max(0, selected[0] + step))

    def choose(index: int, event: typing.Any) -> None:
        if 0 <= index < len(MODEL_EFFORT_OPTIONS):
            event.app.exit(result=MODEL_EFFORT_OPTIONS[index][0])

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

    for number in range(1, len(MODEL_EFFORT_OPTIONS) + 1):
        @bindings.add(str(number))
        def _(event, selected_number=number) -> None:
            choose(selected_number - 1, event)

    @bindings.add("c-c")
    @bindings.add("q")
    def _(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(
        lambda: render_model_effort_menu(current, selected[0]),
        focusable=True
    )

    app: Application[str | None] = Application(
        layout=Layout(
            Window(
                content=control,
                height=len(MODEL_EFFORT_OPTIONS) + 4,
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=MODEL_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    return await app.run_async(pre_run=lambda: clear_pending_input(app.input))


def render_model_effort_menu(current_effort: typing.Any, selected: int) -> StyleAndTextTuples:
    """生成推理强度菜单内容。"""
    current     = normalize_reasoning_effort(current_effort)
    value_width = max(len(value) for value, _ in MODEL_EFFORT_OPTIONS)

    lines: StyleAndTextTuples = [
        ("class:model.title", "Reasoning Effort"),
        ("class:model.status", f" · current={current}"),
        ("", "\n"),
        ("class:model.help", "↑/↓ select · Enter apply · q cancel"),
        ("", "\n")
    ]

    for index, (value, detail) in enumerate(MODEL_EFFORT_OPTIONS):
        active = index == selected
        prefix_style = "class:model.index.active" if active else "class:model.index"
        row_style = "class:model.active" if active else ""
        marker = ">" if active else " "

        lines.extend([
            (prefix_style, f"{marker} {index + 1} "),
            (row_style or "class:model.value", f" {value.ljust(value_width)}"),
            ("class:model.detail", f"  · {detail}"),
            ("", "\n")
        ])

    return lines


def render_model_effort_status(effort: typing.Any) -> None:
    """打印当前推理强度状态。"""
    normalized = normalize_reasoning_effort(effort)
    Design.console.print(
        f"[bold #AFC7D8]Reasoning Effort[/] "
        f"[bold #F4F7FA]· {normalized}[/]"
    )
    Design.console.print()


def _default_effort_index(current_effort: str) -> int:
    """返回当前 effort 在菜单中的默认高亮位置。"""
    for index, (value, _) in enumerate(MODEL_EFFORT_OPTIONS):
        if value == current_effort:
            return index
    for index, (value, _) in enumerate(MODEL_EFFORT_OPTIONS):
        if value == DEFAULT_REASONING_EFFORT:
            return index
    return 0


if __name__ == '__main__':
    pass
