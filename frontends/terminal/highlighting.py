# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import replace
from enum import Enum

from pygments import lex
from pygments.lexer import (
    Lexer,
    RegexLexer,
)
from pygments.lexers import (
    get_lexer_by_name,
    get_lexer_for_filename,
)
from pygments.lexers.special import TextLexer
from pygments.token import (
    _TokenType,
    Error,
    Token,
    Whitespace,
)
from pygments.util import (
    ClassNotFound,
    shebang_matches,
)

from agent.ports.presentation import (
    TextSpan,
    TextStyle,
)
from metadata import const
from .capabilities import TerminalCapabilities
from .color_support import TerminalColorLevel
from .palette import is_light_color
from .styles import PREVIEW_TEXT_STYLE

MAX_HIGHLIGHT_BYTES = 512 * 1024
MAX_HIGHLIGHT_LINES = 10_000
MAX_HIGHLIGHT_LINE_BYTES = 4 * 1024

_TOKEN_TYPE = type(Token.Text)

_MAX_LANGUAGE_INFO_LENGTH: typing.Final[int] = 64

_INCREMENTAL_LEXER_CLASSES: typing.Final[frozenset[str]] = frozenset({
    "PythonLexer",
})


class SyntaxTheme(str, Enum):
    """描述终端会话共享的语法主题，不包含背景填充。"""

    MOCHA = "mocha"
    LATTE = "latte"
    ANSI = "ansi"
    NONE = "none"


class _CodeRole(str, Enum):
    """描述词法器与主题之间的稳定语法角色。"""

    KEYWORD = "keyword"
    FUNCTION = "function"
    TYPE = "type"
    BUILTIN = "builtin"
    VARIABLE = "variable"
    TAG = "tag"
    STRING = "string"
    NUMBER = "number"
    COMMENT = "comment"
    OPERATOR = "operator"
    TEXT = "text"


_CODE_COLORS: dict[SyntaxTheme, dict[_CodeRole, str | None]] = {
    SyntaxTheme.MOCHA: {
        _CodeRole.KEYWORD: "#CBA6F7",
        _CodeRole.FUNCTION: "#89B4FA",
        _CodeRole.TYPE: "#F9E2AF",
        _CodeRole.BUILTIN: "#F38BA8",
        _CodeRole.VARIABLE: "#F5E0DC",
        _CodeRole.TAG: "#89B4FA",
        _CodeRole.STRING: "#A6E3A1",
        _CodeRole.NUMBER: "#FAB387",
        _CodeRole.COMMENT: "#7F849C",
        _CodeRole.OPERATOR: "#89DCEB",
        _CodeRole.TEXT: "#CDD6F4",
    },
    SyntaxTheme.LATTE: {
        _CodeRole.KEYWORD: "#8839EF",
        _CodeRole.FUNCTION: "#1E66F5",
        _CodeRole.TYPE: "#DF8E1D",
        _CodeRole.BUILTIN: "#D20F39",
        _CodeRole.VARIABLE: "#DC8A78",
        _CodeRole.TAG: "#1E66F5",
        _CodeRole.STRING: "#40A02B",
        _CodeRole.NUMBER: "#FE640B",
        _CodeRole.COMMENT: "#6C6F85",
        _CodeRole.OPERATOR: "#179299",
        _CodeRole.TEXT: "#4C4F69",
    },
    SyntaxTheme.ANSI: {
        _CodeRole.KEYWORD: "ansimagenta",
        _CodeRole.FUNCTION: "ansicyan",
        _CodeRole.TYPE: "ansicyan",
        _CodeRole.BUILTIN: "ansibrightcyan",
        _CodeRole.VARIABLE: "ansimagenta",
        _CodeRole.TAG: "ansicyan",
        _CodeRole.STRING: "ansigreen",
        _CodeRole.NUMBER: "ansibrightmagenta",
        _CodeRole.COMMENT: None,
        _CodeRole.OPERATOR: None,
        _CodeRole.TEXT: None,
    },
}


def resolve_syntax_theme(capabilities: TerminalCapabilities) -> SyntaxTheme:
    """从冻结能力选择共享主题，IDE 背景策略不影响语法前景。"""
    level = capabilities.color_support.render_level
    if level is TerminalColorLevel.NONE:
        return SyntaxTheme.NONE
    if level is TerminalColorLevel.ANSI16:
        return SyntaxTheme.ANSI
    background = capabilities.theme.background
    if background is not None and is_light_color(background):
        return SyntaxTheme.LATTE
    return SyntaxTheme.MOCHA


class StreamingCodeHighlighter(object):
    """增量高亮严格追加且只包含完整源码行的代码。"""

    def __init__(self, *, theme: SyntaxTheme = SyntaxTheme.ANSI) -> None:
        self._theme = theme
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
            self._theme is SyntaxTheme.NONE
            or lexer_name is None
            or _exceeds_highlight_limits(source, source.split("\n"))
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
                style=code_token_style(token_type, theme=self._theme),
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
) -> tuple[list[tuple[_TokenType, str]], tuple[str, ...]]:
    """执行 RegexLexer 状态机并返回可用于下一段源码的最终状态。"""
    raw_token_definitions = getattr(lexer, "_tokens", None)
    if not isinstance(raw_token_definitions, dict) or not raw_token_definitions:
        raise TypeError("RegexLexer token definitions are unavailable")
    token_definitions = raw_token_definitions

    position: int = 0
    state_stack = list(initial_stack)
    state_tokens = token_definitions[state_stack[-1]]
    tokens: list[tuple[_TokenType, str]] = []

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
    """保留起始空行并裁去与完整代码块相同的末尾空行。"""
    end = len(lines)
    while end > 1 and not lines[end - 1]:
        end -= 1
    return [list(line) for line in lines[:end]] or [[]]


def highlight_code_lines(
    code: str,
    *,
    path: str = "",
    language: str = "",
    first_line: str | None = None,
    theme: SyntaxTheme = SyntaxTheme.ANSI,
) -> tuple[tuple[TextSpan, ...], ...] | None:
    """按显式语言、文件名或已知首行高亮代码，保留物理行和跨行状态。"""
    source = str(code or "")
    source_lines = source.split("\n")

    if (
        theme is SyntaxTheme.NONE
        or not source
        or _exceeds_highlight_limits(source, source_lines)
    ):
        return None

    lexer = _code_lexer(path=path, language=language, first_line=first_line)
    if lexer is None:
        return None

    spans: list[TextSpan] = []

    try:
        for token_type, value in lex(source, lexer):
            if value:
                _append_span(
                    spans,
                    value,
                    code_token_style(token_type, theme=theme),
                )
    except (TypeError, ValueError):
        return None

    lines = _split_lines(spans)
    if len(lines) == len(source_lines) + 1 and not lines[-1]:
        lines.pop()
    if len(lines) != len(source_lines):
        return None

    return tuple(tuple(line) for line in lines)


def _code_lexer(*, path: str, language: str, first_line: str | None) -> Lexer | None:
    """在第三方边界选择词法器，不读取文件或猜测普通代码片段。"""
    name = _lexer_name(language)
    try:
        lexer = (
            get_lexer_by_name(name, stripnl=False, ensurenl=False)
            if name is not None
            else get_lexer_for_filename(
                path.replace("\\", "/"), stripnl=False, ensurenl=False,
            )
        )
        return None if isinstance(lexer, TextLexer) else lexer
    except ClassNotFound:
        if (
            language
            or first_line is None
            or len(first_line.encode(const.CHARSET)) > MAX_HIGHLIGHT_LINE_BYTES
        ):
            return None
    for pattern, alias in (
        (r"(?:ba|da|k|z)?sh", "bash"),
        (r"python(?:\d+(?:\.\d+)*)?", "python"),
        (r"(?:pwsh|powershell)", "powershell"),
        (r"(?:node|nodejs)", "javascript"),
        (r"ruby(?:\d+(?:\.\d+)*)?", "ruby"),
        (r"perl(?:\d+(?:\.\d+)*)?", "perl"),
    ):
        if shebang_matches(first_line, pattern):
            return get_lexer_by_name(alias, stripnl=False, ensurenl=False)
    return None


def code_parts(
    code: str,
    *,
    current_path: str,
    deleted: bool,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
    theme: SyntaxTheme = SyntaxTheme.ANSI,
) -> list[TextSpan]:
    """按当前文件路径对单行代码预览做语法高亮。"""
    if not code:
        return [part(code, PREVIEW_TEXT_STYLE)]

    highlighted = highlight_code_lines(code, path=current_path, theme=theme)
    if highlighted is None or len(highlighted) != 1:
        style = code_token_style(Token.Text, theme=theme)
        style = _deleted_style(style) if deleted else style
        return [part(code, style)]

    parts = [
        part(span.text, _deleted_style(span.style) if deleted else span.style)
        for span in highlighted[0]
        if span.text
    ]
    return parts or [part(code, code_token_style(Token.Text, theme=theme))]


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
    token_type: _TokenType,
    *,
    theme: SyntaxTheme,
) -> TextStyle:
    """在 Pygments 边界把具体语法角色转换为共享主题前景。"""
    role = _CodeRole.TEXT
    for token, candidate in (
        (Token.Keyword.Type, _CodeRole.TYPE),
        (Token.Keyword, _CodeRole.KEYWORD),
        (Token.Name.Function, _CodeRole.FUNCTION),
        (Token.Name.Class, _CodeRole.TYPE),
        (Token.Name.Builtin, _CodeRole.BUILTIN),
        (Token.Name.Variable, _CodeRole.VARIABLE),
        (Token.Name.Tag, _CodeRole.TAG),
        (Token.Name.Attribute, _CodeRole.TAG),
        (Token.String, _CodeRole.STRING),
        (Token.Literal.Scalar.Plain, _CodeRole.STRING),
        (Token.Number, _CodeRole.NUMBER),
        (Token.Comment, _CodeRole.COMMENT),
        (Token.Operator, _CodeRole.OPERATOR),
        (Token.Punctuation, _CodeRole.OPERATOR),
    ):
        if token_type in token:
            role = candidate
            break
    return TextStyle(
        foreground=None if theme is SyntaxTheme.NONE else _CODE_COLORS[theme][role],
        bold=role is _CodeRole.KEYWORD,
        dim=role is _CodeRole.COMMENT,
    )


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
