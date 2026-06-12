# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova import const
from .command_preview import (
    command_preview,
    inline_script_preview_lines
)
from .tool_trace import (
    PREVIEW_STYLE,
    TracePreview,
    TITLE_STYLE,
    ERROR_STYLE
)

APPROVAL_PENDING_STYLE  = "bold #F2C94C"
APPROVAL_APPROVED_STYLE = "bold #6EE7A8"
APPROVAL_DENIED_STYLE   = ERROR_STYLE
APPROVAL_COMMAND_STYLE  = TITLE_STYLE
APPROVAL_PROMPT_STYLE   = "bold #8FA4B8"
APPROVAL_TOOL_STYLE     = "bold #7DD3FC"
APPROVAL_ARG_STYLE      = "bold #A7F3D0"
APPROVAL_RES_STYLE      = "dim #8FA4B8"

APPROVAL_SUMMARY_MAX_CHARS = 72


def approval_summary(approval: dict[str, typing.Any]) -> str:
    """生成审批请求的简短摘要。"""
    command = command_preview(approval.get("command")).title
    tool    = str(approval.get("tool") or "").strip()

    return _short_approval_summary(command or tool or "tool call")


def _short_approval_summary(value: typing.Any) -> str:
    """截断审批提示里的单行命令摘要。"""
    text = " ".join(str(value or "").split())
    if len(text) <= APPROVAL_SUMMARY_MAX_CHARS:
        return text
    return f"{text[:max(0, APPROVAL_SUMMARY_MAX_CHARS - 4)].rstrip()} ..."


def approval_preview_lines(approval: dict[str, typing.Any]) -> list[str]:
    """提取审批请求的辅助预览信息。"""
    lines: list[str] = []

    cwd      = str(approval.get("cwd") or "").strip()
    reason   = str(approval.get("reason") or "").strip()
    risk     = str(approval.get("risk") or "").strip()
    category = str(approval.get("category") or "").strip()

    if cwd:
        lines.append(f"cwd={cwd}")
    if reason:
        lines.append(f"reason={reason}")
    if risk or category:
        detail = " ".join(
            part for part in [
                f"risk={risk}" if risk else "", f"category={category}" if category else ""
            ] if part
        )
        lines.append(detail)
    return lines


def approval_command_preview(approval: dict[str, typing.Any]) -> TracePreview:
    """提取审批命令里的内联脚本预览，不改变审批标题样式。"""
    preview = command_preview(approval.get("command"))
    if not preview.has_script:
        return TracePreview()
    text = "\n".join(inline_script_preview_lines(preview.script, path=preview.path))
    return TracePreview(full=text, screen=text, omitted_lines=0)


def render_approval_pending_trace(approval: dict[str, typing.Any]) -> str:
    """生成等待审批的轨迹标题。"""
    summary = approval_summary(approval)
    return f"• Approval required {summary}".rstrip()


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


def render_approval_trace_text(
    title: str,
    *,
    approval: dict[str, typing.Any] | None = None,
    include_preview: bool = True
) -> str:
    """生成审批轨迹的纯文本内容。"""
    lines = approval_preview_lines(approval or {}) if include_preview else []
    if not lines:
        return title
    preview = "\n  ".join(lines)
    return f"{title}\n└ {preview}"


def render_approval_trace_parts(
    title: str,
    *,
    approval: dict[str, typing.Any] | None = None,
    state: typing.Literal["pending", "approved", "denied"] = "pending"
) -> list[dict[str, typing.Optional[str]]]:
    """生成审批轨迹的分段样式内容。"""
    if state == "denied":
        title_style = APPROVAL_DENIED_STYLE
    elif state == "approved":
        title_style = APPROVAL_APPROVED_STYLE
    else:
        title_style = APPROVAL_PENDING_STYLE

    parts = _approval_title_parts(title, approval or {}, base_style=title_style)

    lines = approval_preview_lines(approval or {}) if state == "pending" else []
    if lines:
        parts.extend([
            {"text": "\n", "style": None},
            {"text": "└ ", "style": PREVIEW_STYLE},
            {"text": "\n  ".join(lines), "style": PREVIEW_STYLE},
        ])
    return parts


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
        parts.append({"text": title[end:], "style": base_style})
    return parts


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
