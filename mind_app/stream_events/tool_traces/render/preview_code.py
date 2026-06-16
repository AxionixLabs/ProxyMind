# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pygments import lex
from pygments.lexers import (
    get_lexer_by_name,
    guess_lexer_for_filename,
)
from pygments.token import Token
from pygments.util import ClassNotFound

from ..common import (
    PREVIEW_CODE_COMMENT_STYLE,
    PREVIEW_CODE_KEYWORD_STYLE,
    PREVIEW_CODE_NAME_STYLE,
    PREVIEW_CODE_NUMBER_STYLE,
    PREVIEW_CODE_OPERATOR_STYLE,
    PREVIEW_CODE_STRING_STYLE,
    PREVIEW_CODE_TEXT_STYLE,
    PREVIEW_TEXT_STYLE
)


def code_parts(
    code: str,
    *,
    current_path: str,
    deleted: bool,
    part: typing.Callable[[str, str | None], dict[str, typing.Optional[str]]],
) -> list[dict[str, typing.Optional[str]]]:
    """按当前文件路径对代码片段做语法高亮。"""
    if not code:
        return [part(code, PREVIEW_TEXT_STYLE)]

    lexer = _lexer_for_path(current_path, code)
    if lexer is None:
        return [part(code, _deleted_style(PREVIEW_CODE_TEXT_STYLE) if deleted else PREVIEW_CODE_TEXT_STYLE)]

    parts: list[dict[str, typing.Optional[str]]] = []
    try:
        tokens = list(lex(code, lexer))
    except (TypeError, ValueError):
        return [part(code, _deleted_style(PREVIEW_CODE_TEXT_STYLE) if deleted else PREVIEW_CODE_TEXT_STYLE)]

    for token_type, value in tokens:
        if not value:
            continue
        value = value.replace("\n", "")
        if not value:
            continue
        style = _token_style(token_type)
        if deleted and style:
            style = _deleted_style(style)
        parts.append(part(value, style))

    return parts or [part(code, PREVIEW_CODE_TEXT_STYLE)]


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
