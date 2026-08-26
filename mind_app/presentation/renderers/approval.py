# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.stream_events.approval_trace import (
    render_approval_approved_trace,
    render_approval_cancelled_trace,
    render_approval_denied_trace,
    render_approval_trace_parts
)
from ..models import (
    ApprovalView,
    StyledBlock
)


def render_approval_view(view: ApprovalView) -> StyledBlock:
    """把工具审批结果视图转换为中立展示块。"""
    if view.state == "cancelled":
        title = render_approval_cancelled_trace(view.approval)
        style_state = "denied"
    elif view.state == "approved":
        title = render_approval_approved_trace(
            view.approval,
            decision=view.decision,
            source=view.source,
        )
        style_state = "approved"
    else:
        title = render_approval_denied_trace(
            view.approval,
            source=view.source,
        )
        style_state = "denied"

    return StyledBlock(
        plain_text=title,
        spans=tuple(render_approval_trace_parts(
            title,
            approval=view.approval,
            state=style_state,
        )),
        direct=True,
    )


if __name__ == '__main__':
    pass
