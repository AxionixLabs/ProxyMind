# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from mind_core.terminal_input import clear_pending_input

DOWNLOAD_OPTIONS: tuple[tuple[bool, str, str], ...] = (
    (True, "Download MCP", "fetch and install Helix now"),
    (False, "Skip for now", "continue without Helix tools")
)

DOWNLOAD_PROMPT_STYLE = Style.from_dict({
    "download.title"        : "bold #E6F6FF",
    "download.kicker"       : "bold #5FD7AF",
    "download.help"         : "#6F7B88",
    "download.meta"         : "#8896A5",
    "download.index"        : "bold #7A8794",
    "download.index.active" : "bold #101820 bg:#5FD7AF",
    "download.label"        : "bold #F4F7FA",
    "download.detail"       : "#7F8C9A",
    "download.active"       : "#F4F7FA bg:#1D332E",
    "download.status"       : "#8BBFD1"
})


async def choose_runtime_download(
    *,
    executable: str,
    supports: str
) -> bool:
    """显示运行时下载确认菜单，并返回是否继续下载。"""
    selected = [0]
    bindings = KeyBindings()

    def move(step: int) -> None:
        selected[0] = min(len(DOWNLOAD_OPTIONS) - 1, max(0, selected[0] + step))

    def choose(index: int, event) -> None:
        if 0 <= index < len(DOWNLOAD_OPTIONS):
            event.app.exit(result=DOWNLOAD_OPTIONS[index][0])

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
        event.app.exit(result=False)

    control = FormattedTextControl(
        lambda: _render_download_menu(
            selected[0],
            executable=executable,
            supports=supports
        ),
        focusable=True
    )

    app: Application[bool] = Application(
        layout=Layout(
            Window(
                content=control,
                height=6,
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=DOWNLOAD_PROMPT_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    return bool(await app.run_async(pre_run=lambda: clear_pending_input(app.input)))


def _render_download_menu(
    selected: int,
    *,
    executable: str,
    supports: str
) -> StyleAndTextTuples:
    """生成运行时下载菜单内容。"""
    _ = executable, supports
    label_width = max(len(label) for _, label, _detail in DOWNLOAD_OPTIONS)

    lines: StyleAndTextTuples = [
        ("class:download.kicker", "Helix"),
        ("class:download.title", " Runtime Setup"),
        ("", "\n"),
        ("class:download.meta", "Official vertical-domain MCP service"),
        ("", "\n"),
        ("class:download.help", "Up/Down select - Enter confirm - q cancel"),
        ("", "\n")
    ]

    for index, (_, label, detail) in enumerate(DOWNLOAD_OPTIONS):
        active = index == selected
        pointer = ">" if active else " "
        prefix_style = "class:download.index.active" if active else "class:download.index"
        row_style = "class:download.active" if active else ""
        lines.extend([
            (prefix_style, f" {pointer} {index + 1} "),
            (row_style or "class:download.label", f" {label:<{label_width}} "),
            ("class:download.detail", f" - {detail}"),
            ("", "\n")
        ])

    return lines


if __name__ == '__main__':
    pass
