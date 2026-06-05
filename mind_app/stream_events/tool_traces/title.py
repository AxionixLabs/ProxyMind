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
from .common import (
    ACTION_EDIT_STYLE,
    ACTION_GIT_STYLE,
    ACTION_READ_STYLE,
    ACTION_RUN_STYLE,
    ACTION_TOOL_STYLE,
    COUNT_UNIT_STYLE,
    COUNT_VALUE_STYLE,
    DELTA_ADD_STYLE,
    DELTA_REMOVE_STYLE,
    ERROR_DOT_STYLE,
    ERROR_STYLE,
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


def render_tool_trace_parts(
    title: str,
    *,
    preview: typing.Optional[typing.Union[str, TracePreview]] = None,
    ok: bool = True
) -> list[dict[str, typing.Optional[str]]]:
    """把轨迹标题和预览内容转换为带样式的文本片段。"""
    parts = _title_parts(title, ok=ok)

    preview_text = preview.screen if isinstance(preview, TracePreview) else _preview_text(preview)
    if preview_text:
        parts.extend([
            {"text": "\n", "style": None},
            {"text": "└ ", "style": PREVIEW_STYLE},
            *_preview_parts(preview_text),
        ])

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
        parts.append({"text": "•", "style": dot_style})
        body = body[1:]

    match = re.search(r"\(\+(\d+) -(\d+)\)", title)
    if not match:
        count_parts = _title_count_parts(body, base_style=base_style, ok=ok)
        if count_parts:
            parts.extend(count_parts)
            return parts
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
        {"text": "(", "style": base_style},
        {"text": f"+{add_count}", "style": DELTA_ADD_STYLE},
        {"text": " ", "style": base_style},
        {"text": f"-{remove_count}", "style": DELTA_REMOVE_STYLE},
        {"text": ")", "style": base_style},
    ])
    if end < len(body):
        parts.extend(_styled_action_body_parts(body[end:], base_style=base_style, ok=ok))

    return parts


def _title_count_parts(
    body: str,
    *,
    base_style: str,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """拆分标题里的计数摘要，如 (84 matches)。"""
    match = re.search(r"\((\d+) (items|matches|files|symbols|results|sessions)([^)]*)\)", body)
    if not match:
        return []

    start, end = match.span()

    count, unit, tail = match.groups()

    parts: list[dict[str, typing.Optional[str]]] = []

    if start:
        parts.extend(_styled_action_body_parts(body[:start], base_style=base_style, ok=ok))

    parts.extend([
        {"text": "(", "style": base_style},
        {"text": count, "style": COUNT_VALUE_STYLE if ok else base_style},
        {"text": " ", "style": base_style},
        {"text": unit, "style": COUNT_UNIT_STYLE if ok else base_style},
        {"text": tail, "style": base_style},
        {"text": ")", "style": base_style},
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
        return _failure_body_parts(body, base_style=base_style, ok=ok)

    leading_len = len(body) - len(body.lstrip(" "))
    leading     = body[:leading_len]
    rest        = body[leading_len:]

    action, sep, tail = rest.partition(" ")

    parts: list[dict[str, typing.Optional[str]]] = []
    if leading:
        parts.append({"text": leading, "style": base_style})
    if action:
        parts.append({"text": action, "style": action_style})
    if sep or tail:
        parts.extend(_failure_body_parts(f"{sep}{tail}", base_style=base_style, ok=ok))

    return parts


def _failure_body_parts(
    body: str,
    *,
    base_style: str,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """失败标题中仅突出 failed 和 reason，其余内容保持普通标题色。"""
    if ok:
        return [{"text": body, "style": base_style}]

    match = re.search(r"( failed(?:: [^\n]+)?)$", body)
    if not match:
        return [{"text": body, "style": base_style}]

    start = match.start(1)
    parts: list[dict[str, typing.Optional[str]]] = []
    if start:
        parts.append({"text": body[:start], "style": base_style})
    parts.append({"text": body[start:], "style": ERROR_STYLE})
    return parts


def _action_style_for_body(
    body: str
) -> str | None:
    """返回标题动作前缀的弱分类颜色。"""
    text  = body.lstrip()
    first = text.split(" ", 1)[0] if text else ""

    if first in {"Git", "Change"}:
        return ACTION_GIT_STYLE
    if first in {"Read", "Listed", "Searched", "Root", "Skipping", "Skipped"}:
        return ACTION_READ_STYLE
    if first in {"Added", "Edited", "Created", "Deleted", "Copied", "Moved", "Patch"}:
        return ACTION_EDIT_STYLE
    if first in {"Ran", "Recorded", "Rolled", "Updated"}:
        return ACTION_RUN_STYLE
    if first in {"Tool"}:
        return ACTION_TOOL_STYLE

    return None


def _preview_parts(
    preview_text: str
) -> list[dict[str, typing.Optional[str]]]:
    """把预览摘要拆成路径、行号、内容和省略提示片段。"""
    lines = str(preview_text or "").split("\n")
    parts: list[dict[str, typing.Optional[str]]] = []
    current_path = ""
    for index, line in enumerate(lines):
        if index:
            parts.append({"text": "\n  ", "style": PREVIEW_STYLE})
        line_parts, path = _preview_line_parts(line, current_path=current_path)
        if path:
            current_path = path
        parts.extend(line_parts)
    return parts


def _preview_line_parts(
    line: str,
    *,
    current_path: str = ""
) -> tuple[list[dict[str, typing.Optional[str]]], str]:
    """拆分单行预览摘要。"""
    more = re.match(r"^(… \+)(\d+)( lines)$", line)
    if more:
        return [
            {"text": more.group(1), "style": PREVIEW_MORE_STYLE},
            {"text": more.group(2), "style": PREVIEW_COUNT_STYLE},
            {"text": more.group(3), "style": PREVIEW_MORE_STYLE},
        ], ""

    if re.match(r"^@@ .+ @@$", line):
        return [{"text": line, "style": PREVIEW_HUNK_STYLE}], ""

    code_line = re.match(r"^(\s*\d+)(\s)([ +\-])(.*)$", line)
    if code_line:
        line_no, sep, marker, code = code_line.groups()
        return [
            {"text": line_no, "style": PREVIEW_LINE_STYLE},
            {"text": sep, "style": PREVIEW_STYLE},
            {"text": marker, "style": _diff_marker_style(marker)},
            *_code_parts(code, current_path=current_path, deleted=marker == "-"),
        ], ""

    location = re.match(r"^([^:\s][^:\n]*):(\d+)(.*)$", line)
    if location:
        return [
            {"text": location.group(1), "style": PREVIEW_PATH_STYLE},
            {"text": ":", "style": PREVIEW_STYLE},
            {"text": location.group(2), "style": PREVIEW_LINE_STYLE},
            {"text": location.group(3), "style": PREVIEW_TEXT_STYLE},
        ], ""

    summary_entry = re.match(r"^([A-Za-z_][A-Za-z0-9_ -]*)(: )(.+)$", line)
    if summary_entry:
        key = summary_entry.group(1)
        value = summary_entry.group(3)
        return [
            {"text": summary_entry.group(1), "style": PREVIEW_LINE_STYLE},
            {"text": summary_entry.group(2), "style": PREVIEW_STYLE},
            {"text": value, "style": PREVIEW_PATH_STYLE if key == "file" else PREVIEW_TEXT_STYLE},
        ], value if key == "file" else ""

    listed_entry = re.match(r"^(file|dir|symlink|directory)(\s+)(.+)$", line)
    if listed_entry:
        return [
            {"text": listed_entry.group(1), "style": PREVIEW_LINE_STYLE},
            {"text": listed_entry.group(2), "style": PREVIEW_STYLE},
            {"text": listed_entry.group(3), "style": PREVIEW_PATH_STYLE},
        ], ""

    if _looks_like_path(line):
        return [{"text": line, "style": PREVIEW_PATH_STYLE}], line

    return [{"text": line, "style": PREVIEW_TEXT_STYLE}], ""


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
        return [{"text": code, "style": PREVIEW_TEXT_STYLE}]

    lexer = _lexer_for_path(current_path, code)
    if lexer is None:
        return [{"text": code, "style": _deleted_style(PREVIEW_CODE_TEXT_STYLE) if deleted else PREVIEW_CODE_TEXT_STYLE}]

    parts: list[dict[str, typing.Optional[str]]] = []
    try:
        tokens = list(lex(code, lexer))
    except (TypeError, ValueError):
        return [{"text": code, "style": _deleted_style(PREVIEW_CODE_TEXT_STYLE) if deleted else PREVIEW_CODE_TEXT_STYLE}]

    for token_type, value in tokens:
        if not value:
            continue
        value = value.replace("\n", "")
        if not value:
            continue
        style = _token_style(token_type)
        if deleted and style:
            style = _deleted_style(style)
        parts.append({"text": value, "style": style})

    return parts or [{"text": code, "style": PREVIEW_CODE_TEXT_STYLE}]


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
    """把 Pygments token 映射为终端预览样式。"""
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
