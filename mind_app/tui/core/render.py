# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from prompt_toolkit.utils import get_cwidth
from .models import FormattedText


def fragments_text(parts: typing.Iterable[tuple[str, str]]) -> str:
    """返回格式化片段对应的纯文本。"""
    return "".join(text for _style, text in parts)


def clip_fragments(parts: FormattedText, *, width: int) -> FormattedText:
    """按终端显示宽度裁剪单行格式化片段。"""
    limit = max(0, int(width))
    if limit <= 0:
        return []

    out: FormattedText = []
    used = 0

    for style, text in parts:
        for char in str(text).replace("\n", " "):
            char_width = max(0, get_cwidth(char))
            if used + char_width > limit:
                return out
            out.append((style, char))
            used += char_width

    return _merge_fragments(out)


def display_line_count(text: str, *, width: int) -> int:
    """计算文本在指定终端宽度下占用的显示行数。"""
    if not text:
        return 0

    line_width = max(1, int(width))
    rows = 0
    for line in text.split("\n"):
        rows += max(1, math.ceil(get_cwidth(line) / line_width))
    if text.endswith("\n"):
        rows = max(0, rows - 1)
    return rows


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
) -> tuple[int, int]:
    """返回指定视觉行起点对应的逻辑光标位置。"""
    line_width = max(1, int(width))
    target = max(0, int(display_row))
    visual_row = 0
    logical_lines = text.split("\n")

    for line_number, line in enumerate(logical_lines):
        if target == visual_row:
            return 0, line_number

        used_width = 0
        for index, char in enumerate(line):
            char_width = max(0, get_cwidth(char))
            if used_width + char_width > line_width:
                visual_row += 1
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
