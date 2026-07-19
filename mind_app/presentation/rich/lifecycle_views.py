# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.stream_events.failure_display import (
    render_failure_display_parts,
    render_failure_text
)
from mind_app.stream_events.lifecycle_display import render_lifecycle_display_parts
from ..models import (
    FailureView,
    LifecycleView
)
from .models import RenderedBlock


def render_failure_view(view: FailureView) -> RenderedBlock:
    """把运行失败视图转换为当前终端展示。"""
    return RenderedBlock(
        text=render_failure_text(view.phase, view.error),
        display_parts=tuple(render_failure_display_parts(view.phase, view.error)),
        preserve_display_parts=True,
    )


def render_lifecycle_view(view: LifecycleView) -> RenderedBlock:
    """把生命周期事件视图转换为当前终端展示。"""
    title = f"• {view.text}"
    return RenderedBlock(
        text=title,
        display_parts=tuple(render_lifecycle_display_parts(title)),
    )


if __name__ == '__main__':
    pass
