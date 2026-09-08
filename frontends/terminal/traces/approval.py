# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.approvals.summary import (
    approval_amendment_snippet,
    approval_summary,
)
from agent.application.views import (
    ApprovalSource,
    ApprovalState,
)
from agent.ports.presentation import (
    TextSpan,
    TextStyle,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)
from metadata import const

APPROVAL_APPROVED_SYMBOL_STYLE = semantic_text_style(TerminalSemanticRole.SUCCESS)
APPROVAL_DENIED_SYMBOL_STYLE = semantic_text_style(TerminalSemanticRole.FAILURE)
APPROVAL_EMPHASIS_STYLE = TextStyle(bold=True)
APPROVAL_TARGET_STYLE = TextStyle(dim=True)


def render_approval_approved_trace(
    approval: dict[str, typing.Any],
    *,
    decision: str = "accept",
    source: ApprovalSource = "user"
) -> str:
    """生成审批通过后的轨迹标题。"""
    return _trace_text(_approval_approved_parts(
        approval,
        decision=decision,
        source=source,
    ))


def render_approval_denied_trace(
    approval: dict[str, typing.Any],
    *,
    source: ApprovalSource = "user"
) -> str:
    """生成审批拒绝后的轨迹标题。"""
    return _trace_text(_approval_denied_parts(approval, source=source))


def _is_mcp_approval(approval: dict[str, typing.Any]) -> bool:
    """判断审批结果是否属于 MCP 工具调用。"""
    return str(approval.get("kind") or "").strip() == "mcp_tool_call"


def render_approval_cancelled_trace(approval: dict[str, typing.Any]) -> str:
    """生成审批取消后的轨迹标题。"""
    return _trace_text(_approval_cancelled_parts(approval))


def render_approval_trace_parts(
    approval: dict[str, typing.Any],
    *,
    decision: str = "accept",
    source: ApprovalSource = "user",
    state: ApprovalState = "approved",
) -> list[TextSpan]:
    """生成审批轨迹的分段样式内容。"""
    if state == "approved":
        return _approval_approved_parts(
            approval,
            decision=decision,
            source=source,
        )
    if state == "cancelled":
        return _approval_cancelled_parts(approval)
    return _approval_denied_parts(approval, source=source)


def render_approval_failure_parts(
    *,
    subject: str,
    outcome: str,
    relation: str,
    target: str,
    suffix: str | None = None,
) -> list[TextSpan]:
    """按统一终端样式构造未通过审批的决策内容。"""
    parts = [
        TextSpan("✗ ", APPROVAL_DENIED_SYMBOL_STYLE),
        TextSpan(subject),
        TextSpan(outcome, APPROVAL_EMPHASIS_STYLE),
        TextSpan(relation),
        TextSpan(target, APPROVAL_TARGET_STYLE),
    ]
    if suffix is not None:
        parts.append(TextSpan(suffix))
    return parts


def _approval_approved_parts(
    approval: dict[str, typing.Any],
    *,
    decision: str,
    source: ApprovalSource,
) -> list[TextSpan]:
    """按 Codex 历史单元样式构造审批通过内容。"""
    summary = approval_summary(approval)
    parts = [TextSpan("✔ ", APPROVAL_APPROVED_SYMBOL_STYLE)]
    if source in {"hook", "policy"}:
        actor = "Hook " if source == "hook" else "Approval policy "
        parts.extend([
            TextSpan(actor),
            TextSpan("approved", APPROVAL_EMPHASIS_STYLE),
            TextSpan(" "),
            TextSpan(summary, APPROVAL_TARGET_STYLE),
        ])
        return parts

    parts.extend([
        TextSpan("You "),
        TextSpan("approved", APPROVAL_EMPHASIS_STYLE),
    ])
    amendment = approval_amendment_snippet(approval)
    if decision == "acceptWithExecpolicyAmendment" and amendment:
        parts.extend([
            TextSpan(
                f" {const.APP_NAME} to always run commands that start with "
            ),
            TextSpan(amendment, APPROVAL_TARGET_STYLE),
        ])
        return parts

    if decision == "acceptWithExecpolicyAmendment":
        scope = "with the proposed command policy"
    elif decision == "acceptAndRemember":
        scope = "without asking again"
    elif decision == "acceptForSession":
        scope = "every time this session"
    else:
        scope = "this time"
    verb = "call" if _is_mcp_approval(approval) else "run"
    parts.extend([
        TextSpan(f" {const.APP_NAME} to {verb} "),
        TextSpan(summary, APPROVAL_TARGET_STYLE),
        TextSpan(f" {scope}", APPROVAL_EMPHASIS_STYLE),
    ])
    return parts


def _approval_denied_parts(
    approval: dict[str, typing.Any],
    *,
    source: ApprovalSource,
) -> list[TextSpan]:
    """按 Codex 历史单元样式构造审批拒绝内容。"""
    summary = approval_summary(approval)
    if source in {"hook", "policy"}:
        actor = "Hook " if source == "hook" else "Approval policy "
        return render_approval_failure_parts(
            subject=actor,
            outcome="denied",
            relation=" ",
            target=summary,
        )

    verb = "call" if _is_mcp_approval(approval) else "run"
    return render_approval_failure_parts(
        subject="You ",
        outcome="did not approve",
        relation=f" {const.APP_NAME} to {verb} ",
        target=summary,
    )


def _approval_cancelled_parts(
    approval: dict[str, typing.Any],
) -> list[TextSpan]:
    """按审批历史样式构造用户取消内容。"""
    return render_approval_failure_parts(
        subject="You ",
        outcome="cancelled",
        relation=" ",
        target=approval_summary(approval),
        suffix=" · turn was interrupted",
    )


def _trace_text(parts: list[TextSpan]) -> str:
    """把结构化审批片段转换为纯文本。"""
    return "".join(part.text for part in parts).rstrip()


if __name__ == '__main__':
    pass
