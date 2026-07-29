# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova import const
from mind_app.presentation.models import (
    ApprovalSource,
    TextSpan,
    TextStyle
)
from .command_preview import command_preview
from .tool_trace import (
    TITLE_STYLE,
    ERROR_STYLE
)

APPROVAL_APPROVED_STYLE = TextStyle(foreground="#6EE7A8", bold=True)
APPROVAL_DENIED_STYLE   = ERROR_STYLE
APPROVAL_COMMAND_STYLE  = TITLE_STYLE
APPROVAL_TOOL_STYLE     = TextStyle(foreground="#7DD3FC", bold=True)
APPROVAL_ARG_STYLE      = TextStyle(foreground="#A7F3D0", bold=True)
APPROVAL_RES_STYLE      = TextStyle(foreground="#8FA4B8", dim=True)
APPROVAL_SCOPE_STYLE    = TextStyle(foreground="#A7F3D0", bold=True)

APPROVAL_SUMMARY_MAX_CHARS = 72


def approval_summary(approval: dict[str, typing.Any]) -> str:
    """生成审批请求的简短摘要。"""
    command = _approval_command_summary(approval)
    if not command:
        command = command_preview(approval.get("command")).title
    tool = str(approval.get("tool") or "").strip()
    return _short_approval_summary(command or tool or "tool call")


def _short_approval_summary(value: typing.Any) -> str:
    """截断审批提示里的单行命令摘要。"""
    text = " ".join(str(value or "").split())
    if len(text) <= APPROVAL_SUMMARY_MAX_CHARS:
        return text
    return f"{text[:max(0, APPROVAL_SUMMARY_MAX_CHARS - 4)].rstrip()} ..."


def _approval_arguments(approval: dict[str, typing.Any]) -> dict[str, typing.Any]:
    raw = approval.get("arguments", approval.get("args"))
    return dict(raw) if isinstance(raw, dict) else {}


def approval_shell_commands(approval: dict[str, typing.Any]) -> list[typing.Any]:
    """从审批参数里提取单条 shell 命令，兼容预览字段。"""
    arguments        = _approval_arguments(approval)
    argument_command = arguments.get("command")

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


def _approval_command_summary(approval: dict[str, typing.Any]) -> str:
    """生成 shell 命令审批摘要。"""
    commands = approval_shell_commands(approval)
    if not commands:
        return ""
    return command_preview(commands[0]).title or commands[0]


def render_approval_approved_trace(
    approval: dict[str, typing.Any],
    *,
    decision: str = "accept",
    source: ApprovalSource = "user"
) -> str:
    """生成审批通过后的轨迹标题。"""
    summary = approval_summary(approval)
    if source == "hook":
        return f"✔ Hook approved {summary}".rstrip()
    if source == "policy":
        return f"✔ Approval policy approved {summary}".rstrip()

    scope = "for this session" if decision == "acceptForSession" else "this time"
    return f"✔ You approved {const.APP_NAME} to run {summary} {scope}".rstrip()


def render_approval_denied_trace(
    approval: dict[str, typing.Any],
    *,
    source: ApprovalSource = "user"
) -> str:
    """生成审批拒绝后的轨迹标题。"""
    summary = approval_summary(approval)
    if source == "hook":
        return f"• Hook denied {summary}".rstrip()
    if source == "policy":
        return f"• Approval policy denied {summary}".rstrip()

    return f"• You denied {const.APP_NAME} to run {summary}".rstrip()


def render_approval_expired_trace(approval: dict[str, typing.Any]) -> str:
    """生成审批过期后的轨迹标题。"""
    summary = approval_summary(approval)
    return f"• Approval expired for {summary} · command was not run".rstrip()


def render_approval_trace_parts(
    title: str,
    *,
    approval: dict[str, typing.Any] | None = None,
    state: typing.Literal["approved", "denied"] = "approved"
) -> list[TextSpan]:
    """生成审批轨迹的分段样式内容。"""
    if state == "denied":
        title_style = APPROVAL_DENIED_STYLE
    else:
        title_style = APPROVAL_APPROVED_STYLE

    return _approval_title_parts(title, approval or {}, base_style=title_style)


def _approval_title_parts(
    title: str,
    approval: dict[str, typing.Any],
    *,
    base_style: TextStyle
) -> list[TextSpan]:
    """把审批 trace 标题拆成状态文本、工具名和参数。"""
    summary = approval_summary(approval)
    if not summary or summary not in title:
        return [TextSpan(title, base_style)]

    start = title.find(summary)
    end   = start + len(summary)

    parts: list[TextSpan] = []

    if start:
        parts.append(TextSpan(title[:start], base_style))
    parts.extend(_approval_summary_parts(summary, approval, base_style=base_style))
    if end < len(title):
        parts.extend(_approval_suffix_parts(title[end:], base_style=base_style))
    return parts


def _approval_suffix_parts(
    suffix: str,
    *,
    base_style: TextStyle
) -> list[TextSpan]:
    """把审批通过后的作用域提示单独着色。"""
    if base_style != APPROVAL_APPROVED_STYLE:
        return [TextSpan(suffix, base_style)]

    for scope in ("for this session", "this time"):
        if suffix.endswith(scope):
            prefix = suffix[:-len(scope)]
            parts: list[TextSpan] = []
            if prefix:
                parts.append(TextSpan(prefix, base_style))
            parts.append(TextSpan(scope, APPROVAL_SCOPE_STYLE))
            return parts

    return [TextSpan(suffix, base_style)]


def _approval_summary_parts(
    summary: str,
    approval: dict[str, typing.Any],
    *,
    base_style: TextStyle
) -> list[TextSpan]:
    """把审批摘要拆成工具名和参数片段。"""
    if base_style == APPROVAL_APPROVED_STYLE:
        return [TextSpan(summary, APPROVAL_RES_STYLE)]

    tool = str(approval.get("tool") or "").strip()
    if tool and summary.startswith(tool):
        rest = summary[len(tool):]
        parts: list[TextSpan] = [
            TextSpan(tool, APPROVAL_TOOL_STYLE)
        ]
        if rest:
            parts.append(TextSpan(rest, APPROVAL_ARG_STYLE))
        return parts

    return [TextSpan(summary, APPROVAL_COMMAND_STYLE)]


if __name__ == '__main__':
    pass
