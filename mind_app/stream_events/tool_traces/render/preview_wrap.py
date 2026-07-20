# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import unicodedata
from mind_app.presentation.models import TextSpan, TextStyle
from mind_app.presentation.styles import PREVIEW_STYLE


def _text_width(text: str) -> int:
    """计算普通终端文本的显示宽度。"""
    width = 0
    for char in str(text or ""):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def wrap_title_parts(
    parts: list[TextSpan],
    *,
    terminal_width: int,
    continuation_prefix: str,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
    measure_width: typing.Callable[[str], int] | None = None,
) -> list[TextSpan]:
    """按终端宽度换行标题片段，并保留续行左侧轨迹线。"""
    cells = _styled_cells(parts)
    if not cells:
        return []

    width_of = measure_width or _text_width
    first_width = max(16, int(terminal_width or 0))
    next_width = max(1, first_width - width_of(continuation_prefix))
    lines = _wrap_styled_cells(
        cells,
        first_width=first_width,
        next_width=next_width,
        measure_width=width_of,
    )

    wrapped: list[TextSpan] = []
    for index, line in enumerate(lines):
        if index:
            wrapped.append(part("\n", None))
            wrapped.append(part(continuation_prefix, PREVIEW_STYLE))
        wrapped.extend(_coalesce_cells(line, part=part))

    return wrapped


def shell_title_needs_wrap(
    title: str,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> bool:
    """判断 Ran 标题是否需要主动换行并补续行轨迹线。"""
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return False

    text = str(title or "")
    width_of = measure_width or _text_width
    return text.startswith("• Ran ") and width_of(text) > terminal_width


def _wrap_styled_cells(
    cells: list[TextSpan],
    *,
    first_width: int,
    next_width: int,
    measure_width: typing.Callable[[str], int],
) -> list[list[TextSpan]]:
    """把样式单元按首行和续行宽度拆成多行。"""
    lines: list[list[TextSpan]] = []

    remaining = cells
    width     = first_width

    while remaining:
        line, remaining = _take_wrapped_line(
            remaining,
            max_width=width,
            measure_width=measure_width,
        )
        if line:
            lines.append(line)
        width = next_width

    return lines


def _styled_cells(
    parts: list[TextSpan]
) -> list[TextSpan]:
    """把样式片段展开为逐字符显示单元。"""
    cells: list[TextSpan] = []
    for part in parts:
        for char in part.text:
            cells.append(TextSpan(char, part.style))
    return cells


def _take_wrapped_line(
    cells: list[TextSpan],
    *,
    max_width: int,
    measure_width: typing.Callable[[str], int],
) -> tuple[list[TextSpan], list[TextSpan]]:
    """取一行样式单元，优先在空白处换行。"""
    limit = max(1, int(max_width or 1))

    line: list[TextSpan] = []

    width      = 0
    cursor     = 0
    last_space = -1

    while cursor < len(cells):
        cell = cells[cursor]
        char = cell.text

        char_width = max(0, measure_width(char))
        if line and width + char_width > limit:
            break

        line.append(cell)
        width += char_width
        if char.isspace():
            last_space = len(line) - 1
        cursor += 1

        if width >= limit:
            break

    if cursor < len(cells) and last_space > 0:
        rest = line[last_space + 1:] + cells[cursor:]
        line = line[:last_space]
    else:
        rest = cells[cursor:]

    while line and line[-1].text.isspace():
        line.pop()
    while rest and rest[0].text.isspace():
        rest = rest[1:]

    return line, rest


def _coalesce_cells(
    cells: list[TextSpan],
    *,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
) -> list[TextSpan]:
    """把逐字符显示单元合并回连续样式片段。"""
    parts: list[TextSpan] = []
    for cell in cells:
        if not cell.text:
            continue
        if parts and parts[-1].style == cell.style:
            previous = parts[-1]
            parts[-1] = TextSpan(f"{previous.text}{cell.text}", previous.style)
        else:
            parts.append(part(cell.text, cell.style))
    return parts


if __name__ == '__main__':
    pass
