# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.utils import get_cwidth
from frontends.tui.contracts.menu import MenuRequest
from ..fragments import (
    clip_fragments,
    clip_text,
    join_formatted_lines,
    split_formatted_lines,
    wrap_formatted_lines
)


def join_surface_sections(
    header: StyleAndTextTuples,
    rows: StyleAndTextTuples,
    *,
    separate: bool = True
) -> StyleAndTextTuples:
    """拼接菜单头部和选项行，并按需要插入分隔空行。"""
    header_lines = split_formatted_lines(header)
    row_lines = split_formatted_lines(rows)
    if header_lines and not header_lines[-1]:
        header_lines.pop()
    if row_lines and not row_lines[-1]:
        row_lines.pop()
    row_lines = [
        [("class:tui-menu.surface", ""), *line]
        for line in row_lines
    ]
    if not row_lines:
        return join_formatted_lines(header_lines)

    sections = list(header_lines)
    if separate and (
        not sections or any(text.strip() for _style, text in sections[-1])
    ):
        sections.append([])
    sections.extend(row_lines)
    return join_formatted_lines(sections)


def strip_leading_spaces(row: StyleAndTextTuples) -> StyleAndTextTuples:
    """移除包裹续行开头由断词保留的空白。"""
    out: StyleAndTextTuples = []
    strip = True
    for style, text in row:
        if strip:
            text = text.lstrip(" ")
            strip = not text
        if text:
            out.append((style, text))
    return out


def wrapped_text_fragments(
    text: str,
    *,
    style: str,
    width: int,
) -> StyleAndTextTuples:
    """把一段 footer 文本按内容宽度拆成显示行。"""
    rows = wrap_formatted_lines([(style, text)], width=max(1, width))
    out: StyleAndTextTuples = []
    for row in rows:
        out.extend(strip_leading_spaces(row))
        out.append(("", "\n"))
    return out


def body_fragments(request: MenuRequest, *, width: int) -> StyleAndTextTuples:
    """生成正文及可选警示尾段。"""
    out: StyleAndTextTuples = []
    warning_used = False
    body_indent = ""
    body_width = max(1, width)

    lines = request.body_fragments or tuple(
        ((
            request.body_styles[index]
            if index < len(request.body_styles)
            else "class:tui-menu.detail",
            line,
        ),)
        for index, line in enumerate(request.body)
    )
    for index, line_fragments in enumerate(lines):
        line = "".join(text for _style, text in line_fragments)
        if request.body_warning and line and not warning_used:
            body_style = "class:tui-menu.body"
            rows = wrap_formatted_lines(
                [
                    (body_style, line),
                    ("class:tui-menu.warning", f" {request.body_warning}"),
                ],
                width=max(1, body_width),
            )
            for row in rows:
                out.extend([(body_style, body_indent), *row, ("", "\n")])
            warning_used = True
            continue

        if request.body_wrap:
            rows = wrap_formatted_lines(list(line_fragments), width=max(1, body_width))
            max_lines = (
                request.body_line_limits[index]
                if index < len(request.body_line_limits)
                else None
            )
            truncated = max_lines is not None and 0 < max_lines < len(rows)
            if truncated:
                rows = rows[:max_lines]
                clipped = clip_fragments(list(rows[-1]), width=max(0, body_width - 1))
                ellipsis_style = clipped[-1][0] if clipped else line_fragments[-1][0]
                rows[-1] = [*clipped, (ellipsis_style, "…")]
        else:
            truncated = get_cwidth(line) > body_width
            clipped = clip_fragments(
                list(line_fragments),
                width=max(0, body_width - int(truncated)),
            )
            if truncated and body_width > 0:
                ellipsis_style = clipped[-1][0] if clipped else line_fragments[-1][0]
                clipped.append((ellipsis_style, "…"))
            rows = [clipped]

        for row in rows:
            indent_style = row[0][0] if row else "class:tui-menu.detail"
            out.extend([(indent_style, body_indent), *row, ("", "\n")])
    return out


def header_fragments(request: MenuRequest, *, width: int) -> StyleAndTextTuples:
    """生成标题和辅助状态的分层菜单头部。"""
    suffix = request.title_accent_suffix
    if suffix:
        title_text = (
            request.title[:-len(suffix)]
            if request.title.endswith(suffix)
            else request.title
        )
        suffix_width = get_cwidth(suffix)
        title = clip_text(title_text, width=max(0, width - suffix_width))
        suffix = clip_text(suffix, width=max(0, width - get_cwidth(title)))
        out: StyleAndTextTuples = [
            ("class:tui-menu.title", title),
            ("class:tui-menu.title.current", suffix),
        ]
    else:
        out = [("class:tui-menu.title", clip_text(request.title, width=width))]

    if request.status:
        out.extend([
            ("", "\n"),
            (
                request.status_style or (
                    "class:tui-menu.status.current"
                    if request.status.startswith("• ")
                    else "class:tui-menu.status"
                ),
                clip_text(request.status, width=width),
            ),
        ])
    return out


def tab_fragments(request: MenuRequest, *, width: int) -> StyleAndTextTuples:
    """生成当前请求的单行分类页签。"""
    if not request.tabs:
        return []
    out: StyleAndTextTuples = []
    for index, tab in enumerate(request.tabs):
        if index:
            out.append(("class:tui-menu.tab", "  "))
        active = tab.tab_id == request.active_tab_id
        label = f"[{tab.label}]" if active else tab.label
        out.append((
            "class:tui-menu.tab-selected" if active else "class:tui-menu.tab",
            label,
        ))
    return clip_fragments(out, width=width)


if __name__ == '__main__':
    pass
