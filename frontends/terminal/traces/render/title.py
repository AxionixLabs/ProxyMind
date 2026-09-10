# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing

from agent.ports.presentation import (
    TextSpan,
    TextStyle,
)
from frontends.terminal.highlighting import (
    SyntaxTheme,
    code_parts,
)
from frontends.terminal.styles import (
    DELTA_ADD_STYLE,
    DELTA_REMOVE_STYLE,
    PREVIEW_COUNT_STYLE,
    PREVIEW_HUNK_STYLE,
    PREVIEW_LINE_STYLE,
    PREVIEW_MORE_STYLE,
    PREVIEW_PATH_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)
from frontends.terminal.text_layout import (
    clip_display_text,
    text_display_width,
)
from .preview_error import error_preview_line_parts
from .title_parts import title_parts
from ..common import _preview_text
from ..models import TracePreview


def _part(text: str, style: TextStyle | None) -> TextSpan:
    """创建一段带样式的显示片段。"""
    return TextSpan(text, style or TextStyle())


def render_tool_trace_parts(
    title: str,
    *,
    preview: typing.Optional[typing.Union[str, TracePreview]] = None,
    ok: bool | None = True,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
    syntax_theme: SyntaxTheme = SyntaxTheme.ANSI,
) -> list[TextSpan]:
    """把轨迹标题和预览内容转换为带样式的文本片段。"""
    parts = title_parts(title, ok=ok, part=_part)
    preview_text = preview.screen if isinstance(preview, TracePreview) else _preview_text(preview)

    preview_text = _clip_text_preview(
        preview_text,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    if preview_text:
        if parts:
            parts.append(_part("\n", None))

        preview_kind = preview.kind if isinstance(preview, TracePreview) else "text"
        if preview_kind == "plain":
            parts.extend([
                _part("  └ ", PREVIEW_STYLE),
                *_plain_preview_parts(preview_text, indent_prefix="    ")
            ])
        elif preview_kind == "terminal_input":
            parts.extend([
                _part("  └ ", PREVIEW_STYLE),
                *_terminal_input_parts(preview_text),
            ])
        else:
            indent_prefix = "    "
            parts.extend(
                [
                    _part("  └ ", PREVIEW_STYLE),
                    *_preview_parts(
                        preview_text,
                        ok=ok,
                        indent_prefix=indent_prefix,
                        syntax_theme=syntax_theme,
                    )
                ]
            )

    return parts


def _terminal_input_parts(preview_text: str) -> list[TextSpan]:
    """按终端交互样式渲染输入，正文保持默认亮度。"""
    lines = str(preview_text or "").split("\n")
    parts: list[TextSpan] = []

    for index, line in enumerate(lines):
        if index:
            parts.append(_part("\n    ", PREVIEW_STYLE))
        parts.append(_part(line, None))

    return parts


def _clip_text_preview(
    preview_text: str,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> str:
    """按轨迹前缀后的可用宽度裁剪文本预览行。"""
    lines = str(preview_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return ""
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return "\n".join(lines)

    width_of = measure_width or text_display_width
    prefix_width = max(width_of("  └ "), width_of("    "))
    available = max(0, terminal_width - prefix_width)

    return "\n".join(
        clip_display_text(
            line,
            width=available,
            measure_width=width_of,
        )
        for line in lines
    )


def _plain_preview_parts(
    preview_text: str,
    *,
    indent_prefix: str = "    "
) -> list[TextSpan]:
    """把普通输出预览渲染为统一文本样式，不做路径等结构识别。"""
    lines = str(preview_text or "").split("\n")

    parts: list[TextSpan] = []

    for index, line in enumerate(lines):
        if index:
            parts.append(_part(f"\n{indent_prefix}", PREVIEW_STYLE))
        parts.append(_part(line, PREVIEW_TEXT_STYLE))

    return parts


def _preview_parts(
    preview_text: str,
    *,
    ok: bool | None,
    indent_prefix: str = "    ",
    syntax_theme: SyntaxTheme,
) -> list[TextSpan]:
    """把预览摘要拆成路径、行号、内容和省略提示片段。"""
    lines = str(preview_text or "").split("\n")
    parts: list[TextSpan] = []

    current_path: str = ""

    for index, line in enumerate(lines):
        if index:
            parts.append(_part(f"\n{indent_prefix}", PREVIEW_STYLE))

        line_parts, path = _preview_line_parts(
            line,
            current_path=current_path,
            ok=ok,
            syntax_theme=syntax_theme,
        )

        if path:
            current_path = path
        parts.extend(line_parts)

    return parts


def _preview_line_parts(
    line: str,
    *,
    current_path: str = "",
    ok: bool | None = True,
    syntax_theme: SyntaxTheme = SyntaxTheme.ANSI,
) -> tuple[list[TextSpan], str]:
    """拆分单行预览摘要。"""
    if ok is False:
        error_parts = error_preview_line_parts(line, part=_part)
        if error_parts is not None:
            return error_parts, ""

    more = re.match(r"^(… \+)(\d+)( lines)$", line)
    if more:
        return [
            _part(more.group(1), PREVIEW_MORE_STYLE),
            _part(more.group(2), PREVIEW_COUNT_STYLE),
            _part(more.group(3), PREVIEW_MORE_STYLE)
        ], ""

    if re.match(r"^@@ .+ @@$", line):
        return [_part(line, PREVIEW_HUNK_STYLE)], ""

    code_line = re.match(r"^(\s*\d+)(\s)([ +\-])(.*)$", line)
    if code_line:
        line_no, sep, marker, code = code_line.groups()

        return [
            _part(line_no, PREVIEW_LINE_STYLE),
            _part(sep, PREVIEW_STYLE),
            _part(marker, _diff_marker_style(marker)),
            *code_parts(
                code,
                current_path=current_path,
                deleted=marker == "-",
                part=_part,
                theme=syntax_theme,
            ),
        ], ""

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
        ], path

    location = re.match(r"^([^:\s][^:\n]*):(\d+)(.*)$", line)
    if location:
        return [
            _part(location.group(1), PREVIEW_PATH_STYLE),
            _part(":", PREVIEW_STYLE),
            _part(location.group(2), PREVIEW_LINE_STYLE),
            _part(location.group(3), PREVIEW_TEXT_STYLE)
        ], ""

    summary_entry = re.match(r"^([A-Za-z_][A-Za-z0-9_ -]*)(: )(.+)$", line)
    if summary_entry:
        key = summary_entry.group(1)
        value = summary_entry.group(3)

        return [
            _part(summary_entry.group(1), PREVIEW_LINE_STYLE),
            _part(summary_entry.group(2), PREVIEW_STYLE),
            _part(value, PREVIEW_PATH_STYLE if key == "file" else PREVIEW_TEXT_STYLE)
        ], value if key == "file" else ""

    if _looks_like_path(line):
        return [_part(line, PREVIEW_PATH_STYLE)], line

    return [_part(line, PREVIEW_TEXT_STYLE)], ""


def _looks_like_path(line: str) -> bool:
    """判断预览行是否是单独路径。"""
    text = str(line or "").strip()
    if not text or any(char.isspace() for char in text):
        return False
    if text in {".", ".."}:
        return True

    return "/" in text or "\\" in text or bool(re.search(r"\.[A-Za-z0-9_+-]{1,12}$", text))


def _diff_marker_style(marker: str) -> TextStyle:
    """返回 diff 标记的显示样式。"""
    if marker == "+":
        return DELTA_ADD_STYLE
    if marker == "-":
        return semantic_text_style(TerminalSemanticRole.FAILURE, dim=True)

    return PREVIEW_STYLE


if __name__ == '__main__':
    pass
