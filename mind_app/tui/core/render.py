# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.utils import get_cwidth
from .models import FormattedText


def fragments_text(parts: typing.Iterable[tuple[str, str]]) -> str:
    """返回格式化片段对应的纯文本。"""
    return "".join(text for _style, text in parts)


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
    for char in value:
        char_width = max(0, get_cwidth(char))
        if used + char_width > available:
            break
        chars.append(char)
        used += char_width
    return f"{''.join(chars)}{omit}"


def clip_fragments(parts: FormattedText, *, width: int) -> FormattedText:
    """按终端显示宽度裁剪单行格式化片段。"""
    limit = max(0, int(width))
    if limit <= 0:
        return []

    out: FormattedText = []

    used: int = 0

    for style, text in parts:
        for char in str(text).replace("\n", " "):
            char_width = max(0, get_cwidth(char))
            if used + char_width > limit:
                return out
            out.append((style, char))
            used += char_width

    return _merge_fragments(out)


def display_line_count(
    text: str,
    *,
    width: int,
    continuation_widths: typing.Sequence[int] = (),
) -> int:
    """计算文本在指定终端宽度下占用的显示行数。"""
    if not text:
        return 0

    line_width = max(1, int(width))
    rows: int  = 0

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
    widths: list[int]   = []
    current_width: int  = 0
    at_line_start: bool = True

    for style, text in parts:
        chunks     = text.split("\n")
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
    continuation_widths: typing.Sequence[int] = (),
) -> tuple[int, int]:
    """返回指定视觉行起点对应的逻辑光标位置。"""
    line_width    = max(1, int(width))
    target        = max(0, int(display_row))
    visual_row    = 0
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

        for index, char in enumerate(line):
            char_width = max(0, get_cwidth(char))
            available_width = (
                line_width
                if wrap_count == 0
                else max(1, line_width - continuation_width)
            )
            if used_width + char_width > available_width:
                visual_row += 1
                wrap_count += 1
                used_width = 0
                if visual_row >= target:
                    return index, line_number
            used_width += char_width

        if line_number < len(logical_lines) - 1:
            visual_row += 1
            if visual_row >= target:
                return 0, line_number + 1

    last_line = logical_lines[-1]
    return len(last_line), max(0, len(logical_lines) - 1)


def _display_rows(
    text: str,
    *,
    width: int,
    continuation_width: int
) -> int:
    """计算一个逻辑行考虑自动折行前缀后的显示行数。"""
    rows       = 1
    used_width = 0

    for char in text:
        char_width = max(0, get_cwidth(char))
        available_width = (
            width
            if rows == 1
            else max(1, width - continuation_width)
        )
        if used_width + char_width > available_width:
            rows += 1
            used_width = 0
        used_width += char_width

    return rows


def _continuation_width(
    widths: typing.Sequence[int],
    line_number: int
) -> int:
    """返回指定逻辑行的自动折行前缀宽度。"""
    if line_number >= len(widths):
        return 0
    return max(0, int(widths[line_number]))


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


if __name__ == '__main__':
    pass
