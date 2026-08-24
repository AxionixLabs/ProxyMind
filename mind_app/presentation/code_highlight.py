# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import replace
from mind_nova import const
from pygments import lex
from pygments.lexers import get_lexer_for_filename
from pygments.token import Token
from pygments.util import ClassNotFound
from .models import (
    TextSpan,
    TextStyle
)
from .styles import (
    PREVIEW_CODE_COMMENT_STYLE,
    PREVIEW_CODE_KEYWORD_STYLE,
    PREVIEW_CODE_NAME_STYLE,
    PREVIEW_CODE_NUMBER_STYLE,
    PREVIEW_CODE_OPERATOR_STYLE,
    PREVIEW_CODE_STRING_STYLE,
    PREVIEW_CODE_TEXT_STYLE,
    PREVIEW_TEXT_STYLE,
)

MAX_HIGHLIGHT_BYTES      = 512 * 1024
MAX_HIGHLIGHT_LINES      = 10_000
MAX_HIGHLIGHT_LINE_BYTES = 4 * 1024

_LIGHT_CODE_TEXT_STYLE     = TextStyle(foreground="#4C4F69")
_LIGHT_CODE_KEYWORD_STYLE  = TextStyle(foreground="#8839EF", bold=True)
_LIGHT_CODE_NAME_STYLE     = TextStyle(foreground="#1E66F5")
_LIGHT_CODE_STRING_STYLE   = TextStyle(foreground="#40A02B")
_LIGHT_CODE_NUMBER_STYLE   = TextStyle(foreground="#FE640B")
_LIGHT_CODE_COMMENT_STYLE  = TextStyle(foreground="#6C6F85", dim=True)
_LIGHT_CODE_OPERATOR_STYLE = TextStyle(foreground="#179299")


def highlight_code_lines(
    code: str,
    *,
    path: str,
    light_theme: bool = False
) -> tuple[tuple[TextSpan, ...], ...] | None:
    """按文件扩展名高亮完整代码块，并保留跨行解析状态。"""
    source       = str(code or "")
    source_lines = source.split("\n")

    if not source or _exceeds_highlight_limits(source, source_lines):
        return None

    try:
        lexer = get_lexer_for_filename(
            str(path or ""),
            stripnl=False,
            ensurenl=False,
        )
    except ClassNotFound:
        return None

    spans: list[TextSpan] = []

    try:
        for token_type, value in lex(source, lexer):
            if value:
                _append_span(
                    spans,
                    value,
                    _token_style(token_type, light_theme=light_theme),
                )
    except (TypeError, ValueError):
        return None

    lines = _split_lines(spans)
    if len(lines) == len(source_lines) + 1 and not lines[-1]:
        lines.pop()
    if len(lines) != len(source_lines):
        return None

    return tuple(tuple(line) for line in lines)


def code_parts(
    code: str,
    *,
    current_path: str,
    deleted: bool,
    part: typing.Callable[[str, TextStyle | None], TextSpan]
) -> list[TextSpan]:
    """按当前文件路径对单行代码预览做语法高亮。"""
    if not code:
        return [part(code, PREVIEW_TEXT_STYLE)]

    highlighted = highlight_code_lines(code, path=current_path)
    if highlighted is None or len(highlighted) != 1:
        style = _deleted_style(PREVIEW_CODE_TEXT_STYLE) if deleted else PREVIEW_CODE_TEXT_STYLE
        return [part(code, style)]

    parts = [
        part(span.text, _deleted_style(span.style) if deleted else span.style)
        for span in highlighted[0]
        if span.text
    ]
    return parts or [part(code, PREVIEW_CODE_TEXT_STYLE)]


def _exceeds_highlight_limits(code: str, lines: list[str]) -> bool:
    """判断代码块是否超过高亮器的安全输入上限。"""
    return bool(
        len(code.encode(const.CHARSET)) > MAX_HIGHLIGHT_BYTES
        or len(lines) > MAX_HIGHLIGHT_LINES
        or any(
            len(line.encode(const.CHARSET)) > MAX_HIGHLIGHT_LINE_BYTES
            for line in lines
        )
    )


def _token_style(token_type: typing.Any, *, light_theme: bool) -> TextStyle:
    """把 Pygments token 映射为当前明暗主题的代码样式。"""
    if light_theme:
        styles = (
            _LIGHT_CODE_KEYWORD_STYLE,
            _LIGHT_CODE_NAME_STYLE,
            _LIGHT_CODE_STRING_STYLE,
            _LIGHT_CODE_NUMBER_STYLE,
            _LIGHT_CODE_COMMENT_STYLE,
            _LIGHT_CODE_OPERATOR_STYLE,
            _LIGHT_CODE_TEXT_STYLE,
        )
    else:
        styles = (
            PREVIEW_CODE_KEYWORD_STYLE,
            PREVIEW_CODE_NAME_STYLE,
            PREVIEW_CODE_STRING_STYLE,
            PREVIEW_CODE_NUMBER_STYLE,
            PREVIEW_CODE_COMMENT_STYLE,
            PREVIEW_CODE_OPERATOR_STYLE,
            PREVIEW_CODE_TEXT_STYLE,
        )

    keyword, name, string, number, comment, operator, text = styles
    if token_type in Token.Keyword:
        return keyword
    if token_type in Token.Name:
        return name
    if token_type in Token.String:
        return string
    if token_type in Token.Number:
        return number
    if token_type in Token.Comment:
        return comment
    if token_type in Token.Operator or token_type in Token.Punctuation:
        return operator
    return text


def _split_lines(spans: list[TextSpan]) -> list[list[TextSpan]]:
    """按 token 中的换行拆出逐行样式片段。"""
    lines: list[list[TextSpan]] = [[]]
    for span in spans:
        chunks = span.text.split("\n")
        for index, chunk in enumerate(chunks):
            _append_span(lines[-1], chunk, span.style)
            if index < len(chunks) - 1:
                lines.append([])
    return lines


def _append_span(spans: list[TextSpan], text: str, style: TextStyle) -> None:
    """追加非空片段并合并相邻的相同样式。"""
    if not text:
        return None
    if spans and spans[-1].style == style and spans[-1].hyperlink is None:
        previous = spans[-1]
        spans[-1] = TextSpan(f"{previous.text}{text}", style)
        return None
    spans.append(TextSpan(text, style))


def _deleted_style(style: TextStyle) -> TextStyle:
    """弱化删除行中的语法 token。"""
    return style if style.dim else replace(style, dim=True)


if __name__ == '__main__':
    pass
