# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing

from ..common import (
    DELTA_ADD_STYLE,
    DELTA_REMOVE_STYLE,
    PREVIEW_COUNT_STYLE,
    PREVIEW_HUNK_STYLE,
    PREVIEW_LINE_STYLE,
    PREVIEW_MORE_STYLE,
    PREVIEW_PATH_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
    TracePreview,
    _preview_text
)
from .preview_code import code_parts
from .preview_error import error_preview_line_parts
from .preview_tree import tree_preview_line_parts
from .preview_wrap import (
    shell_title_needs_wrap, wrap_title_parts
)
from .title_parts import title_parts


def _part(text: str, style: str | None) -> dict[str, typing.Optional[str]]:
    """创建一段带样式的显示片段。"""
    return {"text": text, "style": style}


def render_tool_trace_parts(
    title: str,
    *,
    preview: typing.Optional[typing.Union[str, TracePreview]] = None,
    ok: bool = True,
    terminal_width: int | None = None
) -> list[dict[str, typing.Optional[str]]]:
    """把轨迹标题和预览内容转换为带样式的文本片段。"""
    parts = title_parts(title, ok=ok, part=_part)

    preview_text = preview.screen if isinstance(preview, TracePreview) else _preview_text(preview)
    if preview_text:
        title_wrapped = shell_title_needs_wrap(title, terminal_width=terminal_width)
        if title_wrapped:
            parts = wrap_title_parts(
                parts,
                terminal_width=max(1, int(terminal_width or 0)),
                continuation_prefix="  │ ",
                part=_part,
            )

        if parts:
            parts.append(_part("\n", None))

        preview_kind = preview.kind if isinstance(preview, TracePreview) else "text"
        if preview_kind == "plain":
            preview_prefix = "  └ " if title_wrapped else "└ "
            preview_indent = "    " if title_wrapped else "  "

            parts.extend([
                _part(preview_prefix, PREVIEW_STYLE),
                *_plain_preview_parts(preview_text, indent_prefix=preview_indent)
            ])

        elif preview_kind in {"file_tree", "patch_tree"}:
            parts.extend(_preview_parts(preview_text, ok=ok, indent_prefix=""))

        elif preview_kind != "tree":
            preview_prefix = "  └ " if title_wrapped else "└ "
            preview_indent = "    " if title_wrapped else "  "

            parts.extend(
                [
                    _part(preview_prefix, PREVIEW_STYLE),
                    *_preview_parts(preview_text, ok=ok, indent_prefix=preview_indent)
                ]
            )

        else:
            parts.extend(_preview_parts(preview_text, ok=ok, indent_prefix=""))

    return parts


def _plain_preview_parts(
    preview_text: str,
    *,
    indent_prefix: str = "  "
) -> list[dict[str, typing.Optional[str]]]:
    """把普通输出预览渲染为统一文本样式，不做路径等结构识别。"""
    lines = str(preview_text or "").split("\n")

    parts: list[dict[str, typing.Optional[str]]] = []

    for index, line in enumerate(lines):
        if index:
            parts.append(_part(f"\n{indent_prefix}", PREVIEW_STYLE))
        parts.append(_part(line, PREVIEW_TEXT_STYLE))

    return parts


def _preview_parts(
    preview_text: str,
    *,
    ok: bool,
    indent_prefix: str = "  "
) -> list[dict[str, typing.Optional[str]]]:
    """把预览摘要拆成路径、行号、内容和省略提示片段。"""
    lines = str(preview_text or "").split("\n")
    parts: list[dict[str, typing.Optional[str]]] = []

    current_path: str = ""
    tree_error_detail = False

    for index, line in enumerate(lines):
        if index:
            parts.append(_part(f"\n{indent_prefix}", PREVIEW_STYLE))

        line_parts, path, tree_error_state = _preview_line_parts(
            line,
            current_path=current_path,
            ok=ok,
            tree_error_detail=tree_error_detail,
        )

        if tree_error_state is not None:
            tree_error_detail = tree_error_state
        if path:
            current_path = path
        parts.extend(line_parts)

    return parts


def _preview_line_parts(
    line: str,
    *,
    current_path: str = "",
    ok: bool = True,
    tree_error_detail: bool = False,
) -> tuple[list[dict[str, typing.Optional[str]]], str, bool | None]:
    """拆分单行预览摘要。"""
    tree_parts = tree_preview_line_parts(
        line,
        is_error_detail=tree_error_detail,
        part=_part
    )

    if tree_parts is not None:
        return tree_parts

    if not ok:
        error_parts = error_preview_line_parts(line, part=_part)
        if error_parts is not None:
            return error_parts, "", None

    more = re.match(r"^(… \+)(\d+)( lines)$", line)
    if more:
        return [
            _part(more.group(1), PREVIEW_MORE_STYLE),
            _part(more.group(2), PREVIEW_COUNT_STYLE),
            _part(more.group(3), PREVIEW_MORE_STYLE)
        ], "", None

    if re.match(r"^@@ .+ @@$", line):
        return [_part(line, PREVIEW_HUNK_STYLE)], "", None

    tree_file_delta = re.match(r"^(└─ )(.+?) \(\+(\d+) -(\d+)\)$", line)

    if tree_file_delta and _looks_like_path(tree_file_delta.group(2)):
        prefix, path, added, removed = tree_file_delta.groups()

        return [
            _part(prefix, PREVIEW_STYLE),
            _part(path, PREVIEW_PATH_STYLE),
            _part(" (", PREVIEW_STYLE),
            _part(f"+{added}", DELTA_ADD_STYLE),
            _part(" ", PREVIEW_STYLE),
            _part(f"-{removed}", DELTA_REMOVE_STYLE),
            _part(")", PREVIEW_STYLE)
        ], path, None

    tree_file = re.match(r"^(└─ )(.+)$", line)
    if tree_file and _looks_like_path(tree_file.group(2)):
        prefix, path = tree_file.groups()

        return [
            _part(prefix, PREVIEW_STYLE),
            _part(path, PREVIEW_PATH_STYLE)
        ], path, None

    code_line = re.match(r"^(\s*\d+)(\s)([ +\-])(.*)$", line)
    if code_line:
        line_no, sep, marker, code = code_line.groups()

        return [
            _part(line_no, PREVIEW_LINE_STYLE),
            _part(sep, PREVIEW_STYLE),
            _part(marker, _diff_marker_style(marker)),
            *code_parts(code, current_path=current_path, deleted=marker == "-", part=_part)
        ], "", None

    file_delta = re.match(r"^(.+?) \(\+(\d+) -(\d+)\)$", line)
    if file_delta and _looks_like_path(file_delta.group(1)):
        path, added, removed = file_delta.groups()

        return [
            _part(path, PREVIEW_PATH_STYLE),
            _part(" (", PREVIEW_STYLE),
            _part(f"+{added}", DELTA_ADD_STYLE),
            _part(" ", PREVIEW_STYLE),
            _part(f"-{removed}", DELTA_REMOVE_STYLE),
            _part(")", PREVIEW_STYLE)
        ], path, None

    location = re.match(r"^([^:\s][^:\n]*):(\d+)(.*)$", line)
    if location:
        return [
            _part(location.group(1), PREVIEW_PATH_STYLE),
            _part(":", PREVIEW_STYLE),
            _part(location.group(2), PREVIEW_LINE_STYLE),
            _part(location.group(3), PREVIEW_TEXT_STYLE)
        ], "", None

    summary_entry = re.match(r"^([A-Za-z_][A-Za-z0-9_ -]*)(: )(.+)$", line)
    if summary_entry:
        key   = summary_entry.group(1)
        value = summary_entry.group(3)

        return [
            _part(summary_entry.group(1), PREVIEW_LINE_STYLE),
            _part(summary_entry.group(2), PREVIEW_STYLE),
            _part(value, PREVIEW_PATH_STYLE if key == "file" else PREVIEW_TEXT_STYLE)
        ], value if key == "file" else "", None

    if _looks_like_path(line):
        return [_part(line, PREVIEW_PATH_STYLE)], line, None

    return [_part(line, PREVIEW_TEXT_STYLE)], "", None


def _looks_like_path(line: str) -> bool:
    """判断预览行是否是单独路径。"""
    text = str(line or "").strip()
    if not text or any(char.isspace() for char in text):
        return False
    if text in {".", ".."}:
        return True

    return "/" in text or "\\" in text or bool(re.search(r"\.[A-Za-z0-9_+-]{1,12}$", text))


def _diff_marker_style(marker: str) -> str:
    """返回 diff 标记的显示样式。"""
    if marker == "+":
        return DELTA_ADD_STYLE
    if marker == "-":
        return "dim #FF8A8A"

    return PREVIEW_STYLE


if __name__ == '__main__':
    pass
