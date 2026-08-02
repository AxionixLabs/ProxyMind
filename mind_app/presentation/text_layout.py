# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import unicodedata
from .models import TextSpan


def text_display_width(text: str) -> int:
    """计算普通终端文本的显示宽度。"""
    width = 0
    for char in str(text or ""):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def wrap_styled_line(
    parts: list[TextSpan],
    *,
    terminal_width: int,
    first_prefix: str = "",
    continuation_prefix: TextSpan = TextSpan(""),
    measure_width: typing.Callable[[str], int] | None = None,
) -> list[TextSpan]:
    """按终端宽度拆分单行样式片段并添加续行前缀。"""
    cells = _styled_cells(parts)
    if not cells:
        return []

    width_of    = measure_width or text_display_width
    line_width  = max(1, int(terminal_width or 0))
    first_width = max(1, line_width - width_of(first_prefix))
    next_width  = max(1, line_width - width_of(continuation_prefix.text))
    lines       = _wrap_styled_cells(
        cells,
        first_width=first_width,
        next_width=next_width,
        measure_width=width_of,
    )

    wrapped: list[TextSpan] = []
    for index, line in enumerate(lines):
        if index:
            wrapped.append(TextSpan("\n"))
            if continuation_prefix.text:
                wrapped.append(continuation_prefix)
        wrapped.extend(_coalesce_cells(line))
    return wrapped


def _wrap_styled_cells(
    cells: list[TextSpan],
    *,
    first_width: int,
    next_width: int,
    measure_width: typing.Callable[[str], int],
) -> list[list[TextSpan]]:
    """按首行和续行宽度拆分样式单元。"""
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


def _styled_cells(parts: list[TextSpan]) -> list[TextSpan]:
    """把样式片段展开为逐字符显示单元。"""
    return [
        TextSpan(char, part.style, part.hyperlink)
        for part in parts
        for char in part.text
    ]


def _take_wrapped_line(
    cells: list[TextSpan],
    *,
    max_width: int,
    measure_width: typing.Callable[[str], int]
) -> tuple[list[TextSpan], list[TextSpan]]:
    """取一行样式单元并优先在空白处断行。"""
    limit = max(1, int(max_width or 1))

    line: list[TextSpan] = []
    width      = 0
    cursor     = 0
    last_space = -1

    while cursor < len(cells):
        cell       = cells[cursor]
        char_width = max(0, measure_width(cell.text))

        if line and width + char_width > limit:
            break

        line.append(cell)
        width += char_width
        if cell.text.isspace():
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


def _coalesce_cells(cells: list[TextSpan]) -> list[TextSpan]:
    """把逐字符显示单元合并为连续样式片段。"""
    parts: list[TextSpan] = []

    for cell in cells:
        if not cell.text:
            continue

        if (
            parts
            and parts[-1].style == cell.style
            and parts[-1].hyperlink == cell.hyperlink
        ):
            previous = parts[-1]

            parts[-1] = TextSpan(
                f"{previous.text}{cell.text}",
                previous.style,
                previous.hyperlink,
            )
        else:
            parts.append(TextSpan(cell.text, cell.style, cell.hyperlink))

    return parts


if __name__ == '__main__':
    pass
