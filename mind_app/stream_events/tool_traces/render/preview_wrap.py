# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from rich.cells import cell_len
from ..common import PREVIEW_STYLE


def wrap_title_parts(
    parts: list[dict[str, typing.Optional[str]]],
    *,
    terminal_width: int,
    continuation_prefix: str,
    part: typing.Callable[[str, str | None], dict[str, typing.Optional[str]]]
) -> list[dict[str, typing.Optional[str]]]:
    """按终端宽度换行标题片段，并保留续行左侧轨迹线。"""
    cells = _styled_cells(parts)
    if not cells:
        return []

    first_width = max(16, int(terminal_width or 0))
    next_width  = max(1, first_width - cell_len(continuation_prefix))
    lines       = _wrap_styled_cells(cells, first_width=first_width, next_width=next_width)

    wrapped: list[dict[str, typing.Optional[str]]] = []
    for index, line in enumerate(lines):
        if index:
            wrapped.append(part("\n", None))
            wrapped.append(part(continuation_prefix, PREVIEW_STYLE))
        wrapped.extend(_coalesce_cells(line, part=part))

    return wrapped


def shell_title_needs_wrap(
    title: str,
    *,
    terminal_width: int | None
) -> bool:
    """判断 Ran 标题是否需要主动换行并补续行轨迹线。"""
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return False

    text = str(title or "")
    return text.startswith("• Ran ") and cell_len(text) > terminal_width


def _wrap_styled_cells(
    cells: list[dict[str, typing.Optional[str]]],
    *,
    first_width: int,
    next_width: int
) -> list[list[dict[str, typing.Optional[str]]]]:
    """把样式单元按首行和续行宽度拆成多行。"""
    lines: list[list[dict[str, typing.Optional[str]]]] = []

    remaining = cells
    width     = first_width

    while remaining:
        line, remaining = _take_wrapped_line(remaining, max_width=width)
        if line:
            lines.append(line)
        width = next_width

    return lines


def _styled_cells(
    parts: list[dict[str, typing.Optional[str]]]
) -> list[dict[str, typing.Optional[str]]]:
    """把样式片段展开为逐字符显示单元。"""
    cells: list[dict[str, typing.Optional[str]]] = []
    for part in parts:
        style = part.get("style")
        for char in str(part.get("text") or ""):
            cells.append({"text": char, "style": style})
    return cells


def _take_wrapped_line(
    cells: list[dict[str, typing.Optional[str]]],
    *,
    max_width: int
) -> tuple[list[dict[str, typing.Optional[str]]], list[dict[str, typing.Optional[str]]]]:
    """取一行样式单元，优先在空白处换行。"""
    limit = max(1, int(max_width or 1))

    line: list[dict[str, typing.Optional[str]]] = []

    width      = 0
    cursor     = 0
    last_space = -1

    while cursor < len(cells):
        cell = cells[cursor]
        char = str(cell.get("text") or "")

        char_width = max(1, cell_len(char))
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

    while line and str(line[-1].get("text") or "").isspace():
        line.pop()
    while rest and str(rest[0].get("text") or "").isspace():
        rest = rest[1:]

    return line, rest


def _coalesce_cells(
    cells: list[dict[str, typing.Optional[str]]],
    *,
    part: typing.Callable[[str, str | None], dict[str, typing.Optional[str]]],
) -> list[dict[str, typing.Optional[str]]]:
    """把逐字符显示单元合并回连续样式片段。"""
    parts: list[dict[str, typing.Optional[str]]] = []
    for cell in cells:
        text = str(cell.get("text") or "")
        style = cell.get("style")
        if not text:
            continue
        if parts and parts[-1].get("style") == style:
            parts[-1]["text"] = f"{parts[-1].get('text') or ''}{text}"
        else:
            parts.append(part(text, style))
    return parts


if __name__ == '__main__':
    pass
