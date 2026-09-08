# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.application.views import ApprovalReviewView
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)
from frontends.terminal.traces.approval import render_approval_failure_parts
from metadata import const

_WARNING_STYLE = semantic_text_style(TerminalSemanticRole.ATTENTION)


def render_approval_review_view(
    view: ApprovalReviewView,
) -> tuple[StyledBlock, ...]:
    """把自动审批评审终态转换为终端展示块。"""
    if view.status in {"approved", "aborted"}:
        return ()
    if view.status == "denied":
        warning = (
            "⚠ Automatic approval review denied "
            f"(risk: {view.risk_level}): {view.rationale}"
        )
        action_parts = render_approval_failure_parts(
            subject="Request ",
            outcome="denied",
            relation=" for ",
            target=f"{const.APP_NAME} to {view.action_summary}",
        )
    else:
        warning = (
            "⚠ Automatic approval review timed out while evaluating "
            "the requested approval."
        )
        action_parts = render_approval_failure_parts(
            subject="Review ",
            outcome="timed out",
            relation=" before ",
            target=f"{const.APP_NAME} could {view.action_summary}",
        )
    return (
        _review_block(warning, style=_WARNING_STYLE),
        _review_action_block(action_parts),
    )


def _review_block(text: str, *, style: TextStyle) -> StyledBlock:
    """构建一项不携带动画的评审历史块。"""
    return StyledBlock(
        plain_text=text,
        spans=(TextSpan(text, style),),
        direct=True,
    )


def _review_action_block(spans: list[TextSpan]) -> StyledBlock:
    """构建遵循统一审批决策样式的自动评审历史块。"""
    return StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=tuple(spans),
        direct=True,
    )


if __name__ == '__main__':
    pass
