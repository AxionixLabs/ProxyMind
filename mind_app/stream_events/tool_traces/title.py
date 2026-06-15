# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from pygments import lex
from pygments.util import ClassNotFound
from pygments.lexers import (
    guess_lexer_for_filename,
    get_lexer_by_name
)
from pygments.token import Token
from rich.cells import cell_len
from .command_parts import render_command_parts
from .tree_preview import tree_preview_line_parts
from .common import (
    ACTION_EDIT_STYLE,
    ACTION_RUN_STYLE,
    ACTION_TOOL_STYLE,
    DELTA_ADD_STYLE,
    DELTA_REMOVE_STYLE,
    ERROR_DOT_STYLE,
    ERROR_PREVIEW_HEAD_STYLE,
    ERROR_PREVIEW_LINE_STYLE,
    ERROR_PREVIEW_MESSAGE_STYLE,
    ERROR_PREVIEW_TEXT_STYLE,
    PREVIEW_COUNT_STYLE,
    PREVIEW_CODE_COMMENT_STYLE,
    PREVIEW_CODE_KEYWORD_STYLE,
    PREVIEW_CODE_NAME_STYLE,
    PREVIEW_CODE_NUMBER_STYLE,
    PREVIEW_CODE_OPERATOR_STYLE,
    PREVIEW_CODE_STRING_STYLE,
    PREVIEW_CODE_TEXT_STYLE,
    PREVIEW_HUNK_STYLE,
    PREVIEW_LINE_STYLE,
    PREVIEW_MORE_STYLE,
    PREVIEW_PATH_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE,
    TracePreview,
    _preview_text
)


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
    parts = _title_parts(title, ok=ok)

    preview_text = preview.screen if isinstance(preview, TracePreview) else _preview_text(preview)
    if preview_text:
        title_wrapped = _shell_title_needs_wrap(title, terminal_width=terminal_width)
        if title_wrapped:
            parts = _wrap_title_parts(
                parts,
                terminal_width=max(1, int(terminal_width or 0)),
                continuation_prefix="  │ "
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


def _shell_title_needs_wrap(
    title: str,
    *,
    terminal_width: int | None
) -> bool:
    """判断 Ran 标题是否需要主动换行并补续行轨迹线。"""
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return False

    text = str(title or "")
    return text.startswith("• Ran ") and cell_len(text) > terminal_width


def _wrap_title_parts(
    parts: list[dict[str, typing.Optional[str]]],
    *,
    terminal_width: int,
    continuation_prefix: str
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
            wrapped.append(_part("\n", None))
            wrapped.append(_part(continuation_prefix, PREVIEW_STYLE))
        wrapped.extend(_coalesce_cells(line))

    return wrapped


def _wrap_styled_cells(
    cells: list[dict[str, typing.Optional[str]]],
    *,
    first_width: int,
    next_width: int
) -> list[list[dict[str, typing.Optional[str]]]]:
    """把样式单元按首行和续行宽度拆成多行。"""
    lines: list[list[dict[str, typing.Optional[str]]]] = []
    remaining = cells
    width = first_width

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
    width = 0
    cursor = 0
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
    cells: list[dict[str, typing.Optional[str]]]
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
            parts.append(_part(text, style))
    return parts


def _title_parts(
    title: str,
    *,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """把标题里的行数增删摘要拆成可独立着色的片段。"""
    base_style = TITLE_STYLE
    dot_style  = SUCCESS_DOT_STYLE if ok else ERROR_DOT_STYLE
    body       = title

    parts: list[dict[str, typing.Optional[str]]] = []
    if body.startswith("•"):
        parts.append(_part("•", dot_style))
        body = body[1:]

    match = re.search(r"\(\+(\d+) -(\d+)\)", title)
    if not match:
        if body:
            parts.extend(_styled_action_body_parts(body, base_style=base_style, ok=ok))
        return parts

    start, end = match.span()

    if title.startswith("•"):
        start = max(0, start - 1)
    end = max(0, end - 1)

    add_count, remove_count = match.groups()

    if start:
        parts.extend(_styled_action_body_parts(body[:start], base_style=base_style, ok=ok))

    parts.extend([
        _part("(", base_style),
        _part(f"+{add_count}", DELTA_ADD_STYLE),
        _part(" ", base_style),
        _part(f"-{remove_count}", DELTA_REMOVE_STYLE),
        _part(")", base_style),
    ])
    if end < len(body):
        parts.extend(_styled_action_body_parts(body[end:], base_style=base_style, ok=ok))

    return parts


def _styled_action_body_parts(
    body: str,
    *,
    base_style: str,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """把标题动作词拆出来，参数仍保留常规标题色。"""
    if not body:
        return []
    action_style = _action_style_for_body(body)
    if not action_style:
        return _plain_body_parts(body, base_style=base_style, ok=ok)

    leading_len = len(body) - len(body.lstrip(" "))
    leading     = body[:leading_len]
    rest        = body[leading_len:]

    action, sep, tail = rest.partition(" ")

    parts: list[dict[str, typing.Optional[str]]] = []
    if leading:
        parts.append(_part(leading, base_style))
    if action:
        parts.append(_part(action, action_style))
    if action == "Ran":
        parts.extend(_ran_command_parts(f"{sep}{tail}", base_style=base_style, ok=ok))
        return parts
    if sep or tail:
        parts.extend(_plain_body_parts(f"{sep}{tail}", base_style=base_style, ok=ok))

    return parts


def _ran_command_parts(
    body: str,
    *,
    base_style: str,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """把 Ran 后面的命令拆成独立颜色。"""
    if not body:
        return []

    command_body = body
    _ = ok

    leading_len = len(command_body) - len(command_body.lstrip(" "))
    leading     = command_body[:leading_len]
    command     = command_body[leading_len:]

    parts: list[dict[str, typing.Optional[str]]] = []
    if leading:
        parts.append(_part(leading, base_style))
    if command:
        parts.extend(render_command_parts(command))

    return parts


def _plain_body_parts(
    body: str,
    *,
    base_style: str,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """标题正文保持原文本，失败状态由状态点表达。"""
    _ = ok
    return [_part(body, base_style)]


def _action_style_for_body(
    body: str
) -> str | None:
    """返回标题动作前缀的弱分类颜色。"""
    text  = body.lstrip()
    first = text.split(" ", 1)[0] if text else ""

    if first in {"Added", "Edited", "Deleted", "Patch"}:
        return ACTION_EDIT_STYLE
    if first in {"Explored", "Ran"}:
        return ACTION_RUN_STYLE
    if first == "Tool":
        return ACTION_TOOL_STYLE

    return None


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
        error_parts = _error_preview_line_parts(line)
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

    code_line = re.match(r"^(\s*\d+)(\s)([ +\-])(.*)$", line)
    if code_line:
        line_no, sep, marker, code = code_line.groups()
        return [
            _part(line_no, PREVIEW_LINE_STYLE),
            _part(sep, PREVIEW_STYLE),
            _part(marker, _diff_marker_style(marker)),
            *_code_parts(code, current_path=current_path, deleted=marker == "-"),
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
            _part(")", PREVIEW_STYLE),
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
        key = summary_entry.group(1)
        value = summary_entry.group(3)
        return [
            _part(summary_entry.group(1), PREVIEW_LINE_STYLE),
            _part(summary_entry.group(2), PREVIEW_STYLE),
            _part(value, PREVIEW_PATH_STYLE if key == "file" else PREVIEW_TEXT_STYLE),
        ], value if key == "file" else "", None

    if _looks_like_path(line):
        return [_part(line, PREVIEW_PATH_STYLE)], line, None

    return [_part(line, PREVIEW_TEXT_STYLE)], "", None


def _error_preview_line_parts(
    line: str
) -> list[dict[str, typing.Optional[str]]] | None:
    """按命令错误输出常见结构分层着色。"""
    stripped = line.strip()
    if not stripped:
        return [_part(line, PREVIEW_STYLE)]

    if re.match(r"^[A-Za-z][A-Za-z0-9_. -]*:$", stripped):
        return [_part(line, ERROR_PREVIEW_HEAD_STYLE)]

    if stripped == "Line |":
        return [_part(line, ERROR_PREVIEW_LINE_STYLE)]

    if stripped == "Traceback (most recent call last):":
        return [_part(line, ERROR_PREVIEW_HEAD_STYLE)]

    if re.match(r"^[A-Za-z]+Error:$", stripped):
        return [_part(line, ERROR_PREVIEW_HEAD_STYLE)]

    if re.match(r"^At line:\d+ char:\d+", stripped, re.IGNORECASE):
        return [_part(line, ERROR_PREVIEW_LINE_STYLE)]

    exception = re.match(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))(:)(.*)$", line)
    if exception:
        return [
            _part(f"{exception.group(1)}{exception.group(2)}", ERROR_PREVIEW_HEAD_STYLE),
            _part(exception.group(3), ERROR_PREVIEW_TEXT_STYLE),
        ]

    if re.match(r"^[A-Za-z0-9_.-]+: .*(error|failed|cannot|missing|exception)", stripped, re.IGNORECASE):
        return [_part(line, ERROR_PREVIEW_HEAD_STYLE)]

    if re.match(r"^\s*[\^~]+$", line):
        leading_len = len(line) - len(line.lstrip())
        return [
            _part(line[:leading_len], ERROR_PREVIEW_LINE_STYLE),
            _part(line[leading_len:], ERROR_PREVIEW_MESSAGE_STYLE),
        ]

    powershell_at_marker = re.match(r"^(\+\s*)([\^~]+)$", line)
    if powershell_at_marker:
        return [
            _part(powershell_at_marker.group(1), ERROR_PREVIEW_LINE_STYLE),
            _part(powershell_at_marker.group(2), ERROR_PREVIEW_MESSAGE_STYLE),
        ]

    if re.match(r"^\+\s", line):
        return [_part(line, ERROR_PREVIEW_LINE_STYLE)]

    if re.match(r"^\s*\d+\s+\|\s", line):
        prefix, sep, body = line.partition("|")
        return [
            _part(prefix, PREVIEW_LINE_STYLE),
            _part(sep, ERROR_PREVIEW_LINE_STYLE),
            _part(body, ERROR_PREVIEW_LINE_STYLE),
        ]

    if line.startswith("  ") and not line.lstrip().startswith("|"):
        return [_part(line, ERROR_PREVIEW_LINE_STYLE)]

    if re.match(r"^\s*\|", line):
        prefix, sep, body = line.partition("|")
        if "~" in line or "^" in line:
            return [
                _part(f"{prefix}{sep}", ERROR_PREVIEW_LINE_STYLE),
                _part(body, ERROR_PREVIEW_MESSAGE_STYLE),
            ]
        return [
            _part(f"{prefix}{sep}", ERROR_PREVIEW_LINE_STYLE),
            _part(body, ERROR_PREVIEW_TEXT_STYLE),
        ]

    if re.match(r"(?i)^\s*(error|fatal|warning|cannot|missing|failed|exception)\b", stripped):
        return [_part(line, ERROR_PREVIEW_TEXT_STYLE)]

    if re.match(r"(?i)^Command failed\b", stripped):
        return [_part(line, ERROR_PREVIEW_TEXT_STYLE)]

    if re.match(r"(?i)^stderr:\s*empty$", stripped):
        return [_part(line, ERROR_PREVIEW_TEXT_STYLE)]

    if re.search(r"(?i)\b(error|fatal|warning|cannot|missing|failed|failure|exception|not found|not recognized|permission denied|no such file|syntax error|parse error|not a valid)\b", stripped):
        return [_part(line, ERROR_PREVIEW_TEXT_STYLE)]

    return None


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


def _code_parts(
    code: str,
    *,
    current_path: str,
    deleted: bool
) -> list[dict[str, typing.Optional[str]]]:
    """按当前文件路径对代码片段做语法高亮。"""
    if not code:
        return [_part(code, PREVIEW_TEXT_STYLE)]

    lexer = _lexer_for_path(current_path, code)
    if lexer is None:
        return [_part(code, _deleted_style(PREVIEW_CODE_TEXT_STYLE) if deleted else PREVIEW_CODE_TEXT_STYLE)]

    parts: list[dict[str, typing.Optional[str]]] = []
    try:
        tokens = list(lex(code, lexer))
    except (TypeError, ValueError):
        return [_part(code, _deleted_style(PREVIEW_CODE_TEXT_STYLE) if deleted else PREVIEW_CODE_TEXT_STYLE)]

    for token_type, value in tokens:
        if not value:
            continue
        value = value.replace("\n", "")
        if not value:
            continue
        style = _token_style(token_type)
        if deleted and style:
            style = _deleted_style(style)
        parts.append(_part(value, style))

    return parts or [_part(code, PREVIEW_CODE_TEXT_STYLE)]


def _lexer_for_path(path: str, code: str) -> typing.Any:
    """根据文件路径选择 Pygments lexer。"""
    text = str(path or "").strip()
    if not text:
        return None
    try:
        return guess_lexer_for_filename(text, code)
    except ClassNotFound:
        fallback = _lexer_name_from_extension(text)
        if not fallback:
            return None
        try:
            return get_lexer_by_name(fallback)
        except ClassNotFound:
            return None


def _lexer_name_from_extension(path: str) -> str:
    """根据常见扩展名返回 lexer 名称。"""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""

    return {
        "py"   : "python",
        "pyw"  : "python",
        "js"   : "javascript",
        "jsx"  : "jsx",
        "ts"   : "typescript",
        "tsx"  : "tsx",
        "java" : "java",
        "go"   : "go",
        "c"    : "c",
        "h"    : "c",
        "cc"   : "cpp",
        "cpp"  : "cpp",
        "hpp"  : "cpp",
        "cs"   : "csharp",
        "rs"   : "rust",
        "kt"   : "kotlin",
        "kts"  : "kotlin",
        "sh"   : "bash",
        "ps1"  : "powershell",
        "json" : "json",
        "yaml" : "yaml",
        "yml"  : "yaml",
        "toml" : "toml",
        "md"   : "markdown",
    }.get(suffix, "")


def _token_style(token_type: typing.Any) -> str:
    """把 Pygments token 映射为预览显示样式。"""
    if token_type in Token.Keyword:
        return PREVIEW_CODE_KEYWORD_STYLE
    if token_type in Token.Name:
        return PREVIEW_CODE_NAME_STYLE
    if token_type in Token.String:
        return PREVIEW_CODE_STRING_STYLE
    if token_type in Token.Number:
        return PREVIEW_CODE_NUMBER_STYLE
    if token_type in Token.Comment:
        return PREVIEW_CODE_COMMENT_STYLE
    if token_type in Token.Operator or token_type in Token.Punctuation:
        return PREVIEW_CODE_OPERATOR_STYLE

    return PREVIEW_CODE_TEXT_STYLE


def _deleted_style(style: str) -> str:
    """让删除行里的 token 保持语义颜色但整体弱化。"""
    return style if style.startswith("dim ") else f"dim {style}"


if __name__ == '__main__':
    pass
