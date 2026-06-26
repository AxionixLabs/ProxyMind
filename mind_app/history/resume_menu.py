# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import shutil
import typing
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from mind_core.terminal_input import clear_pending_input

VISIBLE_HISTORY_ROWS = 10

HISTORY_MENU_STYLE = Style.from_dict({
    "history.title"        : "bold #DCE6EE",
    "history.help"         : "#69727D",
    "history.status"       : "#87919D",
    "history.index"        : "bold #8A949F",
    "history.index.active" : "bold #F4F7FA bg:#3A4651",
    "history.title_text"   : "bold #F4F7FA",
    "history.meta"         : "#7F8C9A",
    "history.active"       : "#F4F7FA bg:#26313A"
})


async def choose_history_session(
    records: list[dict[str, typing.Any]]
) -> dict[str, typing.Any] | None:
    """显示可滚动 history 菜单，并返回选中的会话记录。"""
    if not records:
        return None

    selected = [0]
    offset   = [0]
    bindings = KeyBindings()

    def sync_offset() -> None:
        if selected[0] < offset[0]:
            offset[0] = selected[0]
        if selected[0] >= offset[0] + VISIBLE_HISTORY_ROWS:
            offset[0] = selected[0] - VISIBLE_HISTORY_ROWS + 1

    def move(step: int) -> None:
        selected[0] = min(len(records) - 1, max(0, selected[0] + step))
        sync_offset()

    def choose(index: int, event) -> None:
        if 0 <= index < len(records):
            event.app.exit(result=records[index])

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

    @bindings.add("pagedown")
    def _(event) -> None:
        move(VISIBLE_HISTORY_ROWS)
        event.app.invalidate()

    @bindings.add("pageup")
    def _(event) -> None:
        move(-VISIBLE_HISTORY_ROWS)
        event.app.invalidate()

    @bindings.add("c-c")
    @bindings.add("q")
    def _(event) -> None:
        event.app.exit(result=None)

    for number in range(1, min(9, len(records)) + 1):
        @bindings.add(str(number))
        def _(event, selected_number=number) -> None:
            choose(offset[0] + selected_number - 1, event)

    control = FormattedTextControl(
        lambda: _render_history_menu(records, selected[0], offset[0]),
        focusable=True
    )

    app: Application[dict[str, typing.Any] | None] = Application(
        layout=Layout(
            Window(
                content=control,
                height=_menu_height(records),
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=HISTORY_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    return await app.run_async(pre_run=lambda: clear_pending_input(app.input))


def _render_history_menu(
    records: list[dict[str, typing.Any]],
    selected: int,
    offset: int
) -> StyleAndTextTuples:
    """生成 history 菜单内容。"""
    width         = max(60, shutil.get_terminal_size(fallback=(100, 24)).columns)
    visible_count = _visible_row_count(records)
    visible       = records[offset:offset + visible_count]
    index_width   = _index_width(records)

    lines: StyleAndTextTuples = [
        ("class:history.title", "Resume conversation"),
        ("", "\n"),
        ("class:history.help", "↑/↓ scroll · PgUp/PgDn jump · Enter select · q cancel"),
        ("", "\n")
    ]

    for row_index, record in enumerate(visible):
        record_index = offset + row_index
        active = record_index == selected

        prefix_style = "class:history.index.active" if active else "class:history.index"
        row_style    = "class:history.active" if active else ""
        number       = str(record_index + 1).rjust(index_width)
        row_text     = _record_row_text(record, max(0, width - index_width - 2))

        lines.extend([
            (prefix_style, f" {number} "),
            (row_style, row_text),
            ("", "\n")
        ])

    total    = len(records)
    position = selected + 1 if records else 0

    lines.extend([
        ("class:history.status", _position_text(position, total))
    ])

    return lines


def _menu_height(records: list[dict[str, typing.Any]]) -> int:
    """按实际历史数量自适应高度，最多显示 10 条。"""
    return _visible_row_count(records) + 4


def _visible_row_count(records: list[dict[str, typing.Any]]) -> int:
    """返回当前菜单最多可见行数。"""
    return max(1, min(VISIBLE_HISTORY_ROWS, len(records)))


def _index_width(records: list[dict[str, typing.Any]]) -> int:
    """返回 history 序号显示宽度。"""
    return max(1, len(str(max(1, len(records)))))


def _position_text(position: int, total: int) -> str:
    """返回固定宽度的当前位置文本。"""
    total_width = max(1, len(str(max(1, total))))
    return f"item {str(position).rjust(total_width)}/{total}"


def _record_row_text(record: dict[str, typing.Any], width: int) -> str:
    """返回日期在前、query 在尾部的行展示内容。"""
    prefix    = _record_prefix(record)
    title     = _record_title(record)
    remaining = max(0, width - len(prefix) - 1)

    return f" {prefix} {_clip(title, remaining)}"


def _record_title(record: dict[str, typing.Any]) -> str:
    """返回 history 菜单展示标题。"""
    title = str(record.get("title") or "").strip()
    if not title:
        title = str(record.get("cid") or "-").strip()
    return title


def _record_prefix(record: dict[str, typing.Any]) -> str:
    """返回 history 行前置信息。"""
    return _format_updated_at(record.get("updated_at"))


def _format_updated_at(value: typing.Any) -> str:
    """格式化毫秒时间戳。"""
    try:
        timestamp = int(value) / 1000
    except (TypeError, ValueError):
        return "-"
    return time.strftime("%m-%d %H:%M", time.localtime(timestamp))


def _clip(text: str, limit: int) -> str:
    """按字符数裁剪文本。"""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


if __name__ == '__main__':
    pass
