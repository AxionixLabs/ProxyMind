# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
from agent.ports.presentation import (
    TextSpan,
    TextStyle
)
from mind_app.presentation.styles import (
    COMMAND_FLAG_STYLE,
    COMMAND_HEAD_STYLE,
    COMMAND_NUMBER_STYLE,
    COMMAND_OPERATOR_STYLE,
    COMMAND_PATH_STYLE,
    COMMAND_STRING_STYLE,
    COMMAND_STYLE
)

SHELL_OPERATORS = {
    "|", "||", "&&", ";"
}


def render_command_parts(command: str) -> list[TextSpan]:
    """按展示语义拆分命令文本，不改变原始字符顺序。"""
    tokens          = _command_tokens(command)
    first_word_seen = False

    parts: list[TextSpan] = []

    for token in tokens:
        style = COMMAND_STYLE
        if not token.strip():
            style = TextStyle()
        elif token in SHELL_OPERATORS:
            style = COMMAND_OPERATOR_STYLE
            first_word_seen = False
        elif _is_flag(token):
            style = COMMAND_FLAG_STYLE
        elif _is_number(token):
            style = COMMAND_NUMBER_STYLE
        elif _is_quoted(token):
            inner = token[1:-1]
            style = COMMAND_PATH_STYLE if _looks_like_path(inner) else COMMAND_STRING_STYLE
        elif _looks_like_path(token):
            style = COMMAND_PATH_STYLE
        elif not first_word_seen:
            style = COMMAND_HEAD_STYLE
            first_word_seen = True

        parts.append(TextSpan(token, style))

    return parts


def _command_tokens(command: str) -> list[str]:
    """把命令拆成空白、引号字符串、操作符和普通 token。"""
    text = str(command or "")
    tokens: list[str] = []

    index = 0
    while index < len(text):
        char = text[index]

        if char.isspace():
            end = index + 1
            while end < len(text) and text[end].isspace():
                end += 1
            tokens.append(text[index:end])
            index = end
            continue

        if char in {"'", '"'}:
            end = _quoted_end(text, index, char)
            tokens.append(text[index:end])
            index = end
            continue

        operator = _operator_at(text, index)
        if operator:
            tokens.append(operator)
            index += len(operator)
            continue

        end = index + 1
        while end < len(text):
            if text[end].isspace() or text[end] in {"'", '"'} or _operator_at(text, end):
                break
            end += 1
        tokens.append(text[index:end])
        index = end

    return tokens


def _quoted_end(text: str, start: int, quote: str) -> int:
    """返回引号 token 的结束位置；不尝试完整 shell 转义解析。"""
    index   = start + 1
    escaped = False

    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return index + 1
        index += 1

    return len(text)


def _operator_at(text: str, index: int) -> str:
    """返回当前位置的常见 shell 控制符。"""
    for operator in ("&&", "||", "|", ";"):
        if text.startswith(operator, index):
            return operator

    return ""


def _is_flag(token: str) -> bool:
    """判断 token 是否为常见命令参数开关。"""
    if not token or token == "-":
        return False

    return bool(re.match(r"^(--?\w[\w\-]*|/[A-Za-z][\w\-]*)$", token))


def _is_number(token: str) -> bool:
    """判断 token 是否为数字。"""
    return bool(re.match(r"^[+-]?\d+(\.\d+)?$", token or ""))


def _is_quoted(token: str) -> bool:
    """判断 token 是否为完整引号字符串。"""
    return len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}


def _looks_like_path(token: str) -> bool:
    """按展示用途粗略识别路径 token。"""
    text = str(token or "").strip()
    if not text:
        return False
    if text in {".", ".."}:
        return True
    if "\\" in text or "/" in text:
        return True
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True

    return bool(re.search(r"\.[A-Za-z0-9]{1,8}$", text))


if __name__ == '__main__':
    pass
