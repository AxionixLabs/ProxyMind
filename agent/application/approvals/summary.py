# -*- coding: utf-8 -*-

import typing
import unicodedata

from agent.application.views.commands import command_preview

from .amendments import approval_execpolicy_amendment

APPROVAL_SNIPPET_MAX_GRAPHEMES = 80


def approval_summary(approval: dict[str, typing.Any]) -> str:
    """生成审批请求的简短摘要。"""
    command = _approval_command_summary(approval)
    if not command:
        command = command_preview(approval.get("command")).title
    tool = str(approval.get("tool") or "").strip()
    return _truncate_approval_snippet(command or tool or "tool call")


def approval_shell_commands(approval: dict[str, typing.Any]) -> list[typing.Any]:
    """从审批参数里提取单条 shell 命令，兼容预览字段。"""
    arguments = _approval_arguments(approval)
    argument_command = arguments.get("command")

    if isinstance(argument_command, list):
        return [argument_command]

    text = str(argument_command or "").strip()
    if text:
        return [text]

    command = approval.get("command", approval.get("resolved_command"))
    if isinstance(command, list):
        return [command]

    text = str(command or "").strip()
    if text:
        return [text]
    return []


def approval_amendment_snippet(approval: dict[str, typing.Any]) -> str:
    """生成命令前缀策略批准后的单行前缀摘要。"""
    amendment = approval_execpolicy_amendment(approval)
    if amendment is None:
        return ""
    return _truncate_approval_snippet(amendment.display)


def _approval_command_summary(approval: dict[str, typing.Any]) -> str:
    """生成 shell 命令审批摘要。"""
    commands = approval_shell_commands(approval)
    if not commands:
        return ""
    preview = command_preview(commands[0]).title
    return preview or str(commands[0])


def _approval_arguments(approval: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """返回审批载荷中的工具参数。"""
    raw = approval.get("arguments", approval.get("args"))
    return dict(raw) if isinstance(raw, dict) else {}


def _truncate_approval_snippet(value: typing.Any) -> str:
    """按审批历史规则生成单行命令摘要。"""
    text = str(value or "").strip()
    if not text:
        return ""

    lines = text.splitlines()
    if len(lines) > 1:
        text = f"{lines[0]} ..."

    units = list(_approval_graphemes(text))
    if len(units) <= APPROVAL_SNIPPET_MAX_GRAPHEMES:
        return text
    return "".join(units[:APPROVAL_SNIPPET_MAX_GRAPHEMES - 3]) + "..."


def _approval_graphemes(text: str) -> typing.Iterator[str]:
    """迭代审批摘要中不应被截断的 Unicode 文本单元。"""
    value = str(text or "")
    start = 0
    while start < len(value):
        end = _approval_grapheme_end(value, start)
        yield value[start:end]
        start = end


def _approval_grapheme_end(text: str, start: int) -> int:
    """返回一个审批摘要文本单元的结束位置。"""
    limit = len(text)
    index = min(limit, max(0, int(start)))
    if index >= limit:
        return limit

    first = text[index]
    index += 1
    if _approval_regional_indicator(first):
        if index < limit and _approval_regional_indicator(text[index]):
            index += 1
        return index

    while index < limit:
        char = text[index]
        if _approval_extends_grapheme(char):
            index += 1
            continue
        if char == "\u200d" and index + 1 < limit:
            index += 2
            continue
        break
    return index


def _approval_extends_grapheme(char: str) -> bool:
    """判断字符是否延续前一个审批摘要文本单元。"""
    codepoint = ord(char)
    return bool(
        unicodedata.combining(char)
        or unicodedata.category(char).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _approval_regional_indicator(char: str) -> bool:
    """判断字符是否为区域指示符。"""
    return 0x1F1E6 <= ord(char) <= 0x1F1FF
