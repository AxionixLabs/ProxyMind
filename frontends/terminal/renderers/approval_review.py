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
from frontends.terminal.styles import ERROR_STYLE
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
        action = (
            f"• Request denied for {const.APP_NAME} to "
            f"{view.action_summary}"
        )
    else:
        warning = (
            "⚠ Automatic approval review timed out while evaluating "
            "the requested approval."
        )
        action = (
            f"• Review timed out before {const.APP_NAME} could "
            f"{view.action_summary}"
        )
    return (
        _review_block(warning, style=_WARNING_STYLE),
        _review_block(action, style=ERROR_STYLE),
    )


def _review_block(text: str, *, style: TextStyle) -> StyledBlock:
    """构建一项不携带动画的评审历史块。"""
    return StyledBlock(
        plain_text=text,
        spans=(TextSpan(text, style),),
        direct=True,
    )


if __name__ == '__main__':
    pass
