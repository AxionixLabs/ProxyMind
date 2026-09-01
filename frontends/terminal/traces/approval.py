# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.approvals.summary import (
    approval_amendment_snippet,
    approval_summary,
)
from agent.ports.presentation import (
    TextSpan,
    TextStyle,
)
from agent.application.views import ApprovalSource
from frontends.terminal.styles import (
    ERROR_STYLE,
    TITLE_STYLE,
)
from metadata import const

APPROVAL_APPROVED_STYLE = TextStyle(foreground="#6EE7A8", bold=True)
APPROVAL_DENIED_STYLE = ERROR_STYLE
APPROVAL_COMMAND_STYLE = TITLE_STYLE
APPROVAL_TOOL_STYLE = TextStyle(foreground="#7DD3FC", bold=True)
APPROVAL_ARG_STYLE = TextStyle(foreground="#A7F3D0", bold=True)
APPROVAL_RES_STYLE = TextStyle(foreground="#8FA4B8", dim=True)
APPROVAL_SCOPE_STYLE = TextStyle(foreground="#A7F3D0", bold=True)

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
    if source == "auto_review":
        rationale = _review_rationale(approval)
        suffix = f" · {rationale}" if rationale else ""
        return f"✔ Auto review approved {summary}{suffix}".rstrip()

    if decision == "acceptWithExecpolicyAmendment":
        amendment = approval_amendment_snippet(approval)
        if amendment:
            return (
                f"✔ You approved {const.APP_NAME} to always run commands that "
                f"start with {amendment}"
            ).rstrip()
        scope = "with the proposed command policy"
    elif decision == "acceptForSession":
        scope = "every time this session"
    else:
        scope = "this time"
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
    if source == "auto_review":
        rationale = _review_rationale(approval)
        suffix = f" · {rationale}" if rationale else ""
        return f"• Auto review denied {summary}{suffix}".rstrip()

    return f"• You denied {const.APP_NAME} to run {summary}".rstrip()


def _review_rationale(approval: dict[str, typing.Any]) -> str:
    """读取自动审批解释并压缩为单行展示文本。"""
    return " ".join(str(
        approval.get("rationale") or approval.get("failure_reason") or ""
    ).split())


def render_approval_cancelled_trace(approval: dict[str, typing.Any]) -> str:
    """生成审批取消后的轨迹标题。"""
    summary = approval_summary(approval)
    return f"• You cancelled {summary} · turn was interrupted".rstrip()


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
    amendment = approval_amendment_snippet(approval)
    if (
        base_style == APPROVAL_APPROVED_STYLE
        and amendment
        and "always run commands that start with" in title
        and amendment in title
    ):
        start = title.find(amendment)
        end = start + len(amendment)
        parts: list[TextSpan] = []
        if start:
            parts.append(TextSpan(title[:start], base_style))
        parts.append(TextSpan(amendment, APPROVAL_RES_STYLE))
        if end < len(title):
            parts.append(TextSpan(title[end:], base_style))
        return parts

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

    for scope in ("every time this session", "for this session", "this time"):
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
