# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import unicodedata

from agent.ports.presentation import TextSpan


def text_display_width(text: str) -> int:
    """计算普通终端文本的显示宽度。"""
    width = 0
    for char in str(text or ""):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def clip_display_text(
    text: str,
    *,
    width: int,
    measure_width: typing.Callable[[str], int] | None = None
) -> str:
    """按终端显示宽度裁剪单行文本并保留组合字符边界。"""
    value = str(text or "")
    limit = max(0, int(width))
    width_of = measure_width or text_display_width

    if limit <= 0:
        return ""
    if width_of(value) <= limit:
        return value

    ellipsis_text: str = "…"

    ellipsis_width = max(0, width_of(ellipsis_text))
    if limit <= ellipsis_width:
        return ellipsis_text if ellipsis_width <= limit else ""

    target = limit - ellipsis_width

    used: int = 0

    units: list[str] = []
    for unit in _display_text_units(value):
        unit_width = max(0, width_of(unit))
        if used + unit_width > target:
            break
        units.append(unit)
        used += unit_width

    return f"{''.join(units).rstrip()}{ellipsis_text}"


def wrap_styled_line(
    parts: list[TextSpan],
    *,
    terminal_width: int,
    first_prefix: str = "",
    continuation_prefix: TextSpan = TextSpan(""),
    measure_width: typing.Callable[[str], int] | None = None,
    hard: bool = False
) -> list[TextSpan]:
    """按终端宽度拆分单行样式片段并添加续行前缀。"""
    lines = wrap_styled_lines(
        parts,
        terminal_width=terminal_width,
        first_prefix=first_prefix,
        continuation_prefix=continuation_prefix.text,
        measure_width=measure_width,
        hard=hard,
    )

    wrapped: list[TextSpan] = []
    for index, line in enumerate(lines):
        if index:
            wrapped.append(TextSpan("\n"))
            if continuation_prefix.text:
                wrapped.append(continuation_prefix)
        wrapped.extend(line)
    return wrapped


def layout_styled_line(
    parts: typing.Iterable[TextSpan],
    *,
    first_prefix: TextSpan = TextSpan(""),
    continuation_prefix: TextSpan = TextSpan(""),
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
    hard: bool = False,
) -> list[TextSpan]:
    """为一条逻辑行添加前缀，并按显示宽度生成对齐的物理行。"""
    content = list(parts)
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return [
            *((first_prefix,) if first_prefix.text else ()),
            *content,
        ]

    rows = wrap_styled_lines(
        content,
        terminal_width=terminal_width,
        first_prefix=first_prefix.text,
        continuation_prefix=continuation_prefix.text,
        measure_width=measure_width,
        hard=hard,
    )

    rendered: list[TextSpan] = []
    for index, row in enumerate(rows):
        if index:
            rendered.append(TextSpan("\n"))
        prefix = first_prefix if index == 0 else continuation_prefix
        if prefix.text:
            rendered.append(prefix)
        rendered.extend(row)
    return rendered


def wrap_styled_lines(
    parts: list[TextSpan],
    *,
    terminal_width: int,
    first_prefix: str = "",
    continuation_prefix: str = "",
    measure_width: typing.Callable[[str], int] | None = None,
    hard: bool = False
) -> list[list[TextSpan]]:
    """按不同首行前缀宽度拆分并返回独立的样式行。"""
    cells = _styled_cells(parts)
    if not cells:
        return [[]]

    width_of = measure_width or text_display_width
    line_width = max(1, int(terminal_width or 0))

    lines = _wrap_styled_cells(
        cells,
        first_width=max(1, line_width - width_of(first_prefix)),
        next_width=max(1, line_width - width_of(continuation_prefix)),
        measure_width=width_of,
        hard=hard,
    )

    return [_coalesce_cells(line) for line in lines]


def _display_text_units(text: str) -> typing.Iterator[str]:
    """迭代裁剪时不得拆开的组合文本单元。"""
    value = str(text or "")
    start = 0

    while start < len(value):
        end = _display_text_unit_end(value, start)
        yield value[start:end]
        start = end


def _display_text_unit_end(text: str, start: int) -> int:
    """返回一个组合文本单元在字符串中的结束位置。"""
    limit = len(text)
    index = min(limit, max(0, int(start)))

    if index >= limit:
        return limit

    first = text[index]
    index += 1
    if _regional_indicator(first):
        if index < limit and _regional_indicator(text[index]):
            index += 1
        return index

    while index < limit:
        char = text[index]
        if _extends_display_text_unit(char):
            index += 1
            continue
        if char == "\u200d" and index + 1 < limit:
            index += 2
            continue
        break
    return index


def _extends_display_text_unit(char: str) -> bool:
    """判断字符是否延续前一个组合文本单元。"""
    codepoint = ord(char)
    return bool(
        unicodedata.combining(char)
        or unicodedata.category(char).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _regional_indicator(char: str) -> bool:
    """判断字符是否为区域指示符。"""
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _wrap_styled_cells(
    cells: list[TextSpan],
    *,
    first_width: int,
    next_width: int,
    measure_width: typing.Callable[[str], int],
    hard: bool
) -> list[list[TextSpan]]:
    """按首行和续行宽度拆分样式单元。"""
    lines: list[list[TextSpan]] = []

    remaining = cells
    width = first_width

    while remaining:
        line, remaining = _take_wrapped_line(
            remaining,
            max_width=width,
            measure_width=measure_width,
            hard=hard,
        )
        if line:
            lines.append(line)
        width = next_width
    return lines


def _styled_cells(parts: list[TextSpan]) -> list[TextSpan]:
    """把样式片段展开为不可拆分的组合显示单元。"""
    return [
        TextSpan(unit, part.style, part.hyperlink)
        for part in parts
        for unit in _display_text_units(part.text)
    ]


def _take_wrapped_line(
    cells: list[TextSpan],
    *,
    max_width: int,
    measure_width: typing.Callable[[str], int],
    hard: bool
) -> tuple[list[TextSpan], list[TextSpan]]:
    """取一行样式单元并优先在空白处断行。"""
    limit = max(1, int(max_width or 1))

    line: list[TextSpan] = []

    width = 0
    cursor = 0
    last_space = -1

    while cursor < len(cells):
        cell = cells[cursor]
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

    if not hard and cursor < len(cells) and last_space > 0:
        rest = line[last_space + 1:] + cells[cursor:]
        line = line[:last_space]
    else:
        rest = cells[cursor:]

    if not hard:
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
