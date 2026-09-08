# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.application.views import ApprovalView
from agent.ports.presentation import StyledBlock
from frontends.terminal.traces.approval import render_approval_trace_parts


def render_approval_view(view: ApprovalView) -> StyledBlock:
    """把工具审批结果视图转换为中立展示块。"""
    spans = tuple(render_approval_trace_parts(
        view.approval,
        decision=view.decision,
        source=view.source,
        state=view.state,
    ))
    title = "".join(span.text for span in spans)

    return StyledBlock(
        plain_text=title,
        spans=spans,
        direct=True,
    )


if __name__ == '__main__':
    pass
