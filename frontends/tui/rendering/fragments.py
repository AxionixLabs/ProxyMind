# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import unicodedata

from prompt_toolkit.utils import get_cwidth

from frontends.tui.contracts.text import (
    FormattedText,
    LineFill
)

__all__ = [
    "ZERO_WIDTH_ESCAPE_STYLE",
    "clip_fragments",
    "clip_text",
    "cursor_point",
    "cursor_point_for_display_row",
    "display_line_count",
    "fill_fragments",
    "fragment_continuation_widths",
    "fragments_text",
    "iter_formatted_text_units",
    "iter_text_unit_ranges",
    "iter_text_units",
    "join_formatted_lines",
    "next_text_unit_end",
    "split_formatted_lines",
    "transcript_hint",
    "wrap_formatted_lines",
]

ZERO_WIDTH_ESCAPE_STYLE = "[ZeroWidthEscape]"


def _iter_visible_formatted_text_units(fragments: FormattedText) -> typing.Iterator[FormattedText]:
    """迭代一段不含零宽转义的组合文本单元。"""
    if not fragments:
        return

    plain_text = fragments_text(fragments)

    fragment_index: int = 0
    fragment_offset: int = 0

    for unit_text in iter_text_units(plain_text):
        remaining = len(unit_text)
        unit: FormattedText = []

        while remaining > 0:
            style, text = fragments[fragment_index]

            available = len(text) - fragment_offset
            count = min(remaining, available)
            value = text[fragment_offset:fragment_offset + count]

            if unit and unit[-1][0] == style:
                previous_style, previous_text = unit[-1]
                unit[-1] = previous_style, previous_text + value
            else:
                unit.append((style, value))

            remaining -= count
            fragment_offset += count

            if fragment_offset >= len(text):
                fragment_index += 1
                fragment_offset = 0

        yield unit


def _display_rows(
    text: str,
    *,
    width: int,
    continuation_width: int
) -> int:
    """计算一个逻辑行考虑自动折行前缀后的显示行数。"""
    rows = 1
    used_width = 0

    for unit in iter_text_units(text):
        unit_width = max(0, get_cwidth(unit))
        available_width = (
            width
            if rows == 1
            else max(1, width - continuation_width)
        )
        if used_width + unit_width > available_width:
            rows += 1
            used_width = 0
        used_width += unit_width
    return rows


def _continuation_width(
    widths: typing.Sequence[int],
    line_number: int
) -> int:
    """返回指定逻辑行的自动折行前缀宽度。"""
    if line_number >= len(widths):
        return 0
    return max(0, int(widths[line_number]))


def _append_fragment(parts: FormattedText, style: str, text: str) -> None:
    """追加非空片段。"""
    if text:
        parts.append((style, text))


def _extends_text_unit(char: str) -> bool:
    """判断字符是否延续前一个组合文本单元。"""
    codepoint = ord(char)
    return bool(
        unicodedata.combining(char)
        or unicodedata.category(char).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _is_regional_indicator(char: str) -> bool:
    """判断字符是否为区域指示符。"""
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _merge_fragments(parts: FormattedText) -> FormattedText:
    """合并相邻且样式相同的格式化片段。"""
    out: FormattedText = []
    for style, text in parts:
        if not text:
            continue
        if out and out[-1][0] == style:
            previous_style, previous_text = out[-1]
            out[-1] = previous_style, previous_text + text
        else:
            out.append((style, text))
    return out


def fragments_text(parts: typing.Iterable[tuple[str, str]]) -> str:
    """返回格式化片段对应的纯文本。"""
    return "".join(
        text
        for style, text in parts
        if ZERO_WIDTH_ESCAPE_STYLE not in style
    )


def next_text_unit_end(text: str, start: int) -> int:
    """返回下一个组合文本单元的结束位置。"""
    limit = len(text)
    index = min(limit, max(0, int(start)))

    if index >= limit:
        return limit

    first = text[index]
    index += 1
    if first == "\r" and index < limit and text[index] == "\n":
        return index + 1
    if _is_regional_indicator(first):
        if index < limit and _is_regional_indicator(text[index]):
            index += 1
        return index

    while index < limit:
        char = text[index]
        if _extends_text_unit(char):
            index += 1
            continue
        if char == "\u200d" and index + 1 < limit:
            index += 2
            continue
        break
    return index


def iter_text_unit_ranges(text: str, ) -> typing.Iterator[tuple[int, int, str]]:
    """按原文位置迭代不可拆分的组合文本单元。"""
    value = str(text or "")
    start = 0
    while start < len(value):
        end = next_text_unit_end(value, start)
        yield start, end, value[start:end]
        start = end


def iter_text_units(text: str) -> typing.Iterator[str]:
    """迭代不可拆分的组合文本单元。"""
    for _start, _end, unit in iter_text_unit_ranges(text):
        yield unit


def iter_formatted_text_units(parts: FormattedText) -> typing.Iterator[FormattedText]:
    """迭代保留原始样式边界的组合文本单元。"""
    fragments: FormattedText = []
    for style, text in parts:
        if not text:
            continue
        if ZERO_WIDTH_ESCAPE_STYLE in style:
            yield from _iter_visible_formatted_text_units(fragments)
            fragments = []
            yield [(style, text)]
            continue
        fragments.append((style, text))
    yield from _iter_visible_formatted_text_units(fragments)


def split_formatted_lines(parts: FormattedText) -> list[FormattedText]:
    """按显式换行拆分格式化片段并保留每行样式。"""
    lines: list[FormattedText] = []
    current: FormattedText = []

    found: bool = False

    for style, text in parts:
        if not text:
            if "[SetCursorPosition]" in style:
                current.append((style, text))
                found = True
            continue
        found = True
        if ZERO_WIDTH_ESCAPE_STYLE in style:
            current.append((style, text))
            continue
        chunks = text.split("\n")
        for index, chunk in enumerate(chunks):
            if chunk:
                current.append((style, chunk))
            if index < len(chunks) - 1:
                lines.append(current)
                current = []
    if found:
        lines.append(current)
    return lines


def join_formatted_lines(lines: typing.Iterable[FormattedText]) -> FormattedText:
    """连接格式化逻辑行并在相邻行之间插入换行。"""
    out: FormattedText = []
    for index, line in enumerate(lines):
        if index:
            out.append(("", "\n"))
        out.extend(line)
    return out


def wrap_formatted_lines(
    parts: FormattedText,
    *,
    width: int
) -> list[FormattedText]:
    """按终端宽度把格式化逻辑行拆成完整显示行。"""
    limit = max(1, int(width))

    rows: list[FormattedText] = []

    for line in split_formatted_lines(parts):
        if not line:
            rows.append([])
            continue
        current: FormattedText = []

        used = 0
        for unit in iter_formatted_text_units(line):
            unit_width = max(0, get_cwidth(fragments_text(unit)))
            if current and unit_width and used + unit_width > limit:
                rows.append(_merge_fragments(current))
                current = []
                used = 0
            current.extend(unit)
            used += unit_width
        rows.append(_merge_fragments(current))

    return rows


def clip_text(text: typing.Any, *, width: int) -> str:
    """按终端显示宽度裁剪单行文本并保留省略标记。"""
    value = str(text or "").replace("\n", " ")
    limit = max(0, int(width))

    if limit <= 0:
        return ""

    if get_cwidth(value) <= limit:
        return value

    omit = "…"
    ellipsis_width = get_cwidth(omit)

    if limit <= ellipsis_width:
        return omit

    available = limit - ellipsis_width
    used = 0
    chars: list[str] = []

    for unit in iter_text_units(value):
        unit_width = max(0, get_cwidth(unit))
        if used + unit_width > available:
            break
        chars.append(unit)
        used += unit_width
    return f"{''.join(chars)}{omit}"


def clip_fragments(parts: FormattedText, *, width: int) -> FormattedText:
    """按终端显示宽度裁剪单行格式化片段。"""
    limit = max(0, int(width))
    if limit <= 0:
        return []
    out: FormattedText = []
    used = 0
    normalized = [
        (style, str(text).replace("\n", " "))
        for style, text in parts
    ]
    for unit in iter_formatted_text_units(normalized):
        unit_width = max(0, get_cwidth(fragments_text(unit)))
        if used + unit_width > limit:
            return _merge_fragments(out)
        out.extend(unit)
        used += unit_width
    return _merge_fragments(out)


def transcript_hint(
    marker: str,
    key_label: str,
    terminal_width: int | None,
    *,
    prefix: str = "  "
) -> str:
    """返回当前省略行能够完整容纳的记录入口提示。"""
    key = str(key_label or "").strip()
    if not key:
        return ""
    full = f" ({key} to view transcript)"
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return full
    if get_cwidth(f"{prefix}{marker}{full}") <= terminal_width:
        return full
    compact = f" {key}"
    if get_cwidth(f"{prefix}{marker}{compact}") <= terminal_width:
        return compact
    return ""


def fill_fragments(
    parts: FormattedText,
    *,
    width: int,
    fill: LineFill
) -> FormattedText:
    """裁剪或延伸单行片段，使其占满指定的可用宽度。"""
    target = max(1, int(width) - max(0, int(fill.margin)))
    out = clip_fragments(parts, width=target)

    used = get_cwidth(fragments_text(out))
    if used >= target:
        return out

    character = str(fill.character or " ")

    character_width = get_cwidth(character)
    if character_width <= 0:
        character = " "
        character_width = 1

    count = (target - used) // character_width
    remainder = target - used - count * character_width

    style = (
        fill.style
        if fill.style is not None
        else next((style for style, text in reversed(out) if text), "")
    )
    if count:
        _append_fragment(out, style, character * count)
    if remainder:
        _append_fragment(out, style, " " * remainder)
    return out


def display_line_count(
    text: str,
    *,
    width: int,
    continuation_widths: typing.Sequence[int] = ()
) -> int:
    """计算文本在指定终端宽度下占用的显示行数。"""
    if not text:
        return 0
    line_width = max(1, int(width))

    rows: int = 0

    for line_number, line in enumerate(text.split("\n")):
        continuation_width = _continuation_width(
            continuation_widths,
            line_number,
        )
        rows += _display_rows(
            line,
            width=line_width,
            continuation_width=continuation_width,
        )
    if text.endswith("\n"):
        rows = max(0, rows - 1)
    return rows


def fragment_continuation_widths(
    parts: FormattedText,
    *,
    prefix_style: str,
    prefix_width: int
) -> tuple[int, ...]:
    """按逻辑行首样式返回自动折行前缀宽度。"""
    widths: list[int] = []
    current_width: int = 0
    at_line_start: bool = True

    for style, text in parts:
        chunks = text.split("\n")
        last_index = len(chunks) - 1
        for index, chunk in enumerate(chunks):
            if at_line_start and chunk:
                current_width = (
                    max(0, int(prefix_width))
                    if style == prefix_style
                    else 0
                )
                at_line_start = False
            if index < last_index:
                widths.append(current_width)
                current_width = 0
                at_line_start = True
    widths.append(current_width)
    return tuple(widths)


def cursor_point(text: str, *, width: int) -> tuple[int, int]:
    """返回文本末尾在格式化内容中的逻辑列和行。"""
    _ = width
    logical_lines = text.split("\n")
    return len(logical_lines[-1]), max(0, len(logical_lines) - 1)


def cursor_point_for_display_row(
    text: str,
    *,
    width: int,
    display_row: int,
    continuation_widths: typing.Sequence[int] = ()
) -> tuple[int, int]:
    """返回指定视觉行起点对应的逻辑光标位置。"""
    line_width: int = max(1, int(width))
    target: int = max(0, int(display_row))
    visual_row: int = 0
    logical_lines = text.split("\n")

    for line_number, line in enumerate(logical_lines):
        if target == visual_row:
            return 0, line_number
        continuation_width = _continuation_width(
            continuation_widths,
            line_number,
        )
        used_width = 0
        wrap_count = 0
        for start, _end, unit in iter_text_unit_ranges(line):
            unit_width = max(0, get_cwidth(unit))
            available_width = (
                line_width
                if wrap_count == 0
                else max(1, line_width - continuation_width)
            )
            if used_width + unit_width > available_width:
                visual_row += 1
                wrap_count += 1
                used_width = 0
                if visual_row >= target:
                    return start, line_number
            used_width += unit_width
        if line_number < len(logical_lines) - 1:
            visual_row += 1
            if visual_row >= target:
                return 0, line_number + 1
    last_line = logical_lines[-1]
    return len(last_line), max(0, len(logical_lines) - 1)


if __name__ == '__main__':
    pass
