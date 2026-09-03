# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import unicodedata
from collections.abc import Mapping

from agent.application.views.commands import command_preview
from .amendments import approval_execpolicy_amendment

APPROVAL_SNIPPET_MAX_GRAPHEMES = 80

ApprovalReviewActionKind: typing.TypeAlias = typing.Literal[
    "command",
    "write_stdin",
    "apply_patch",
    "network_access",
    "request_permissions",
    "mcp_tool_call",
]


def approval_summary(approval: dict[str, typing.Any]) -> str:
    """生成审批请求的简短摘要。"""
    if str(approval.get("kind") or "").strip() == "mcp_tool_call":
        server = str(approval.get("server") or "").strip()
        tool_name = str(approval.get("tool_name") or "").strip()
        summary = ": ".join(value for value in (server, tool_name) if value)
        return _truncate_approval_snippet(summary or "MCP tool call")
    command = _approval_command_summary(approval)
    if not command:
        command = command_preview(approval.get("command")).title
    tool = str(approval.get("tool") or "").strip()
    return _truncate_approval_snippet(command or tool or "tool call")


def approval_review_action_summary(
    kind: ApprovalReviewActionKind,
    action: Mapping[str, typing.Any],
) -> str:
    """把已校验的自动评审动作转换为稳定动词摘要。"""
    if not isinstance(action, Mapping):
        raise TypeError("approval review action must be an object")
    if kind == "command":
        command = command_preview(action.get("command")).title
        return f"run {_truncate_approval_snippet(command or 'the command')}"
    if kind == "write_stdin":
        session_id = _summary_text(
            action.get("session_id") or action.get("process_id")
        )
        target = f"terminal {session_id}" if session_id else "the terminal"
        return f"write to {_truncate_approval_snippet(target)}"
    if kind == "apply_patch":
        files = action.get("files")
        paths = tuple(
            _summary_text(item)
            for item in files
        ) if isinstance(files, (list, tuple)) else ()
        paths = tuple(path for path in paths if path)
        if len(paths) == 1:
            return f"apply a patch to {_truncate_approval_snippet(paths[0])}"
        if paths:
            return f"apply a patch to {len(paths)} files"
        return "apply the requested patch"
    if kind == "network_access":
        target = _summary_text(action.get("target") or action.get("host"))
        return f"access {_truncate_approval_snippet(target or 'the network target')}"
    if kind == "request_permissions":
        scope = _summary_text(action.get("scope"))
        suffix = f" for {_truncate_approval_snippet(scope)}" if scope else ""
        return f"use the requested permissions{suffix}"
    if kind == "mcp_tool_call":
        server = _summary_text(action.get("server"))
        tool_name = _summary_text(action.get("tool_name"))
        target = ".".join(item for item in (server, tool_name) if item)
        return f"call MCP tool {_truncate_approval_snippet(target or 'unknown')}"
    raise ValueError("approval review action kind is invalid")


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


def _summary_text(value: typing.Any) -> str:
    """把动作载荷字段压缩为单行摘要。"""
    return " ".join(str(value or "").split())


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


if __name__ == '__main__':
    pass
