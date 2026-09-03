# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import replace

from pygments import lex
from pygments.lexer import RegexLexer
from pygments.lexers import (
    get_lexer_by_name,
    get_lexer_for_filename,
)
from pygments.token import (
    Error,
    Token,
    Whitespace,
)
from pygments.util import ClassNotFound

from agent.ports.presentation import (
    TextSpan,
    TextStyle,
)
from metadata import const
from .styles import PREVIEW_TEXT_STYLE

MAX_HIGHLIGHT_BYTES = 512 * 1024
MAX_HIGHLIGHT_LINES = 10_000
MAX_HIGHLIGHT_LINE_BYTES = 4 * 1024

_TOKEN_TYPE = type(Token.Text)

_MAX_LANGUAGE_INFO_LENGTH: typing.Final[int] = 64

_INCREMENTAL_LEXER_CLASSES: typing.Final[frozenset[str]] = frozenset({
    "PythonLexer",
})

_LIGHT_CODE_TEXT_STYLE = TextStyle(foreground="#4C4F69")
_LIGHT_CODE_KEYWORD_STYLE = TextStyle(foreground="#8839EF", bold=True)
_LIGHT_CODE_NAME_STYLE = TextStyle(foreground="#1E66F5")
_LIGHT_CODE_STRING_STYLE = TextStyle(foreground="#40A02B")
_LIGHT_CODE_NUMBER_STYLE = TextStyle(foreground="#FE640B")
_LIGHT_CODE_COMMENT_STYLE = TextStyle(foreground="#6C6F85", dim=True)
_LIGHT_CODE_OPERATOR_STYLE = TextStyle(foreground="#179299")

PREVIEW_CODE_TEXT_STYLE = TextStyle(foreground="#BCC9D6")
PREVIEW_CODE_KEYWORD_STYLE = TextStyle(foreground="#B9A6D8", bold=True)
PREVIEW_CODE_NAME_STYLE = TextStyle(foreground="#CAD5DF")
PREVIEW_CODE_STRING_STYLE = TextStyle(foreground="#A9CDBB")
PREVIEW_CODE_NUMBER_STYLE = TextStyle(foreground="#D3C27C")
PREVIEW_CODE_COMMENT_STYLE = TextStyle(foreground="#8FA4B8", dim=True)
PREVIEW_CODE_OPERATOR_STYLE = TextStyle(foreground="#AAB8C6")


class StreamingCodeHighlighter(object):
    """增量高亮严格追加且只包含完整源码行的代码。"""

    def __init__(self) -> None:
        self._lexer: RegexLexer | None = None
        self._lexer_name: str | None = None
        self._source: str = ""
        self._state_stack: tuple[str, ...] = ("root",)
        self._lines: list[list[TextSpan]] = [[]]

    def reset(self) -> None:
        """清除当前代码块的 lexer 状态和已渲染行。"""
        self._lexer = None
        self._lexer_name = None
        self._source = ""
        self._state_stack = ("root",)
        self._lines = [[]]

    def render(
        self,
        text: str,
        *,
        language: str,
    ) -> list[list[TextSpan]] | None:
        """返回增量高亮行；不满足保守条件时返回 ``None``。"""
        source = str(text or "")
        lexer_name = _lexer_name(language)

        if (
            lexer_name is None
            or "\r" in source
            or "\0" in source
            or (source and not source.endswith("\n"))
        ):
            self.reset()
            return None

        if self._lexer_name is not None and (
            lexer_name != self._lexer_name
            or not source.startswith(self._source)
        ):
            self.reset()
            return None

        lexer = self._lexer
        if lexer is None:
            lexer = _incremental_lexer(lexer_name)
            if lexer is None:
                self.reset()
                return None
            self._lexer = lexer
            self._lexer_name = lexer_name

        suffix = source[len(self._source):]
        try:
            tokens, state_stack = _lex_regex_suffix(
                lexer,
                suffix,
                initial_stack=self._state_stack,
            )
        except (AssertionError, IndexError, TypeError, ValueError):
            self.reset()
            return None

        for token_type, value in tokens:
            self._append_text(
                value,
                style=code_token_style(token_type, light_theme=False),
            )

        self._source = source
        self._state_stack = state_stack
        return _visible_stream_lines(self._lines)

    def _append_text(self, text: str, *, style: TextStyle) -> None:
        """追加 lexer 输出并保留跨 token 的物理行边界。"""
        chunks = text.split("\n")
        for index, chunk in enumerate(chunks):
            if chunk:
                _append_span(self._lines[-1], chunk, style)
            if index < len(chunks) - 1:
                self._lines.append([])


def _lexer_name(language: str) -> str | None:
    """从受限 info string 中提取 lexer 名称。"""
    language_info = str(language or "").strip()
    if (
        not language_info
        or len(language_info) > _MAX_LANGUAGE_INFO_LENGTH
    ):
        return None
    return language_info.split(maxsplit=1)[0]


def _incremental_lexer(name: str) -> RegexLexer | None:
    """返回允许跨行续接且无需过滤器的 RegexLexer。"""
    try:
        lexer = get_lexer_by_name(name)
    except ClassNotFound:
        return None

    if (
        not isinstance(lexer, RegexLexer)
        or type(lexer).__name__ not in _INCREMENTAL_LEXER_CLASSES
        or type(lexer).get_tokens_unprocessed
        is not RegexLexer.get_tokens_unprocessed
        or lexer.filters
        or lexer.tabsize
    ):
        return None
    return lexer


def _lex_regex_suffix(
    lexer: RegexLexer,
    text: str,
    *,
    initial_stack: tuple[str, ...],
) -> tuple[list[tuple[typing.Any, str]], tuple[str, ...]]:
    """执行 RegexLexer 状态机并返回可用于下一段源码的最终状态。"""
    raw_token_definitions = getattr(lexer, "_tokens", None)
    if not isinstance(raw_token_definitions, dict) or not raw_token_definitions:
        raise TypeError("RegexLexer token definitions are unavailable")
    token_definitions = raw_token_definitions

    position: int = 0
    state_stack = list(initial_stack)
    state_tokens = token_definitions[state_stack[-1]]
    tokens: list[tuple[typing.Any, str]] = []

    while True:
        for regex_match, action, new_state in state_tokens:
            match = regex_match(text, position)
            if match is None:
                continue

            if action is not None:
                if type(action) is _TOKEN_TYPE:
                    tokens.append((action, match.group()))
                else:
                    tokens.extend(
                        (token_type, value)
                        for _offset, token_type, value in action(lexer, match)
                    )
            position = match.end()

            if new_state is not None:
                _update_state_stack(state_stack, new_state=new_state)
                state_tokens = token_definitions[state_stack[-1]]
            break
        else:
            if position >= len(text):
                break
            if text[position] == "\n":
                state_stack = ["root"]
                state_tokens = token_definitions["root"]
                tokens.append((Whitespace, "\n"))
            else:
                tokens.append((Error, text[position]))
            position += 1

    return tokens, tuple(state_stack)


def _update_state_stack(
    state_stack: list[str],
    *,
    new_state: tuple[str, ...] | int | str,
) -> None:
    """按 Pygments RegexLexer 规则更新状态栈。"""
    if isinstance(new_state, tuple):
        for state in new_state:
            if state == "#pop":
                if len(state_stack) > 1:
                    state_stack.pop()
            elif state == "#push":
                state_stack.append(state_stack[-1])
            else:
                state_stack.append(state)
        return None

    if isinstance(new_state, int):
        if abs(new_state) >= len(state_stack):
            del state_stack[1:]
        else:
            del state_stack[new_state:]
        return None

    if new_state == "#push":
        state_stack.append(state_stack[-1])
        return None
    raise AssertionError(f"invalid Pygments state transition: {new_state!r}")


def _visible_stream_lines(
    lines: list[list[TextSpan]],
) -> list[list[TextSpan]]:
    """返回与完整高亮一致的非边界空行副本。"""
    start = 0
    end = len(lines)
    while start < end - 1 and not lines[start]:
        start += 1
    while end > start + 1 and not lines[end - 1]:
        end -= 1
    return [list(line) for line in lines[start:end]] or [[]]


def highlight_code_lines(
    code: str,
    *,
    path: str,
    light_theme: bool = False
) -> tuple[tuple[TextSpan, ...], ...] | None:
    """按文件扩展名高亮完整代码块，并保留跨行解析状态。"""
    source = str(code or "")
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
                    code_token_style(token_type, light_theme=light_theme),
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


def code_token_style(
    token_type: typing.Any,
    *,
    light_theme: bool,
) -> TextStyle:
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
