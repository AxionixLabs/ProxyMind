# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.stream_events.failure_display import (
    render_failure_display_parts,
    render_failure_text
)
from mind_app.stream_events.lifecycle_display import render_lifecycle_display_parts
from ..models import (
    FailureView,
    LifecycleView,
    StyledBlock
)


def render_failure_view(view: FailureView) -> StyledBlock:
    """把运行失败视图转换为中立展示块。"""
    return StyledBlock(
        plain_text=render_failure_text(view.phase, view.error),
        spans=tuple(render_failure_display_parts(view.phase, view.error)),
        preserve_spans=True,
    )


def render_lifecycle_view(view: LifecycleView) -> StyledBlock:
    """把生命周期事件视图转换为中立展示块。"""
    title = f"• {view.text}"
    return StyledBlock(
        plain_text=title,
        spans=tuple(render_lifecycle_display_parts(title)),
    )


if __name__ == '__main__':
    pass
