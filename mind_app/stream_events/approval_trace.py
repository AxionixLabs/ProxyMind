# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova import const
from .command_preview import (
    command_preview,
    inline_script_preview_lines
)
from .tool_trace import (
    TracePreview,
    TITLE_STYLE,
    ERROR_STYLE
)

APPROVAL_APPROVED_STYLE = "bold #6EE7A8"
APPROVAL_DENIED_STYLE   = ERROR_STYLE
APPROVAL_COMMAND_STYLE  = TITLE_STYLE
APPROVAL_TOOL_STYLE     = "bold #7DD3FC"
APPROVAL_ARG_STYLE      = "bold #A7F3D0"
APPROVAL_RES_STYLE      = "dim #8FA4B8"
APPROVAL_SCOPE_STYLE    = "bold #A7F3D0"

APPROVAL_SUMMARY_MAX_CHARS = 72


def approval_summary(approval: dict[str, typing.Any]) -> str:
    """生成审批请求的简短摘要。"""
    command = command_preview(approval.get("command")).title
    if not command:
        command = _batch_command_summary(approval)

    tool = str(approval.get("tool") or "").strip()
    return _short_approval_summary(command or tool or "tool call")


def _short_approval_summary(value: typing.Any) -> str:
    """截断审批提示里的单行命令摘要。"""
    text = " ".join(str(value or "").split())
    if len(text) <= APPROVAL_SUMMARY_MAX_CHARS:
        return text
    return f"{text[:max(0, APPROVAL_SUMMARY_MAX_CHARS - 4)].rstrip()} ..."


def approval_command_preview(approval: dict[str, typing.Any]) -> TracePreview:
    """提取审批命令里的内联脚本预览，不改变审批标题样式。"""
    preview = command_preview(approval.get("command"))
    if not preview.has_script:
        previews = []
        for command in _approval_commands(approval):
            item_preview = command_preview(command)
            if item_preview.has_script:
                previews.append(inline_script_preview_lines(
                    item_preview.script, path=item_preview.path
                ))

        lines = [line for group in previews for line in group]
        if not lines:
            return TracePreview()
        text = "\n".join(lines)
        return TracePreview(full=text, screen=text, omitted_lines=0)

    text = "\n".join(inline_script_preview_lines(preview.script, path=preview.path))
    return TracePreview(full=text, screen=text, omitted_lines=0)


def _approval_arguments(approval: dict[str, typing.Any]) -> dict[str, typing.Any]:
    raw = approval.get("arguments", approval.get("args"))
    return dict(raw) if isinstance(raw, dict) else {}


def _approval_commands(approval: dict[str, typing.Any]) -> list[str]:
    """从批量 shell_command 审批参数里提取命令列表。"""
    arguments = _approval_arguments(approval)
    raw_items = arguments.get("items")
    if not isinstance(raw_items, list):
        raw_items = approval.get("items")
    if not isinstance(raw_items, list):
        return []

    commands: list[str] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if command:
            commands.append(command)
    return commands


def _batch_command_summary(approval: dict[str, typing.Any]) -> str:
    """为批量 shell_command 审批生成可读摘要。"""
    commands = _approval_commands(approval)
    if not commands:
        return ""
    if len(commands) == 1:
        return command_preview(commands[0]).title or commands[0]
    sample = "; ".join(command_preview(command).title or command for command in commands[:2])
    suffix = f"; +{len(commands) - 2} more" if len(commands) > 2 else ""
    return f"{len(commands)} commands: {sample}{suffix}"


def render_approval_approved_trace(
    approval: dict[str, typing.Any],
    *,
    decision: str = "accept"
) -> str:
    """生成审批通过后的轨迹标题。"""
    summary = approval_summary(approval)
    scope   = "for this session" if decision == "acceptForSession" else "this time"
    return f"✔ You approved {const.APP_NAME} to run {summary} {scope}".rstrip()


def render_approval_denied_trace(approval: dict[str, typing.Any]) -> str:
    """生成审批拒绝后的轨迹标题。"""
    summary = approval_summary(approval)
    return f"• You denied {const.APP_NAME} to run {summary}".rstrip()


def render_approval_trace_parts(
    title: str,
    *,
    approval: dict[str, typing.Any] | None = None,
    state: typing.Literal["approved", "denied"] = "approved"
) -> list[dict[str, typing.Optional[str]]]:
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
    base_style: str
) -> list[dict[str, typing.Optional[str]]]:
    """把审批 trace 标题拆成状态文本、工具名和参数。"""
    summary = approval_summary(approval)
    if not summary or summary not in title:
        return [{"text": title, "style": base_style}]

    start = title.find(summary)
    end   = start + len(summary)

    parts: list[dict[str, typing.Optional[str]]] = []

    if start:
        parts.append({"text": title[:start], "style": base_style})
    parts.extend(_approval_summary_parts(summary, approval, base_style=base_style))
    if end < len(title):
        parts.extend(_approval_suffix_parts(title[end:], base_style=base_style))
    return parts


def _approval_suffix_parts(
    suffix: str,
    *,
    base_style: str
) -> list[dict[str, typing.Optional[str]]]:
    """把审批通过后的作用域提示单独着色。"""
    if base_style != APPROVAL_APPROVED_STYLE:
        return [{"text": suffix, "style": base_style}]

    for scope in ("for this session", "this time"):
        if suffix.endswith(scope):
            prefix = suffix[:-len(scope)]
            parts: list[dict[str, typing.Optional[str]]] = []
            if prefix:
                parts.append({"text": prefix, "style": base_style})
            parts.append({"text": scope, "style": APPROVAL_SCOPE_STYLE})
            return parts

    return [{"text": suffix, "style": base_style}]


def _approval_summary_parts(
    summary: str,
    approval: dict[str, typing.Any],
    *,
    base_style: str
) -> list[dict[str, typing.Optional[str]]]:
    """把审批摘要拆成工具名和参数片段。"""
    if base_style == APPROVAL_APPROVED_STYLE:
        return [{"text": summary, "style": APPROVAL_RES_STYLE}]

    tool = str(approval.get("tool") or "").strip()
    if tool and summary.startswith(tool):
        rest = summary[len(tool):]
        parts: list[dict[str, typing.Optional[str]]] = [
            {"text": tool, "style": APPROVAL_TOOL_STYLE}
        ]
        if rest:
            parts.append({"text": rest, "style": APPROVAL_ARG_STYLE})
        return parts

    return [{"text": summary, "style": APPROVAL_COMMAND_STYLE}]


if __name__ == '__main__':
    pass
