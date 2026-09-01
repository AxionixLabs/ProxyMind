# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.views import (
    FailureView,
    LifecycleView,
    RunIncompleteView,
)
from agent.ports.presentation import StyledBlock
from frontends.terminal.renderers.failure import render_failure_block
from frontends.terminal.renderers.lifecycle_parts import render_lifecycle_display_parts


def render_failure_view(
    view: FailureView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把运行失败视图转换为中立展示块。"""
    return render_failure_block(
        view.phase,
        view.error,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )


def render_incomplete_view(
    view: RunIncompleteView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> StyledBlock:
    """把未完整结束的运行转换为中立展示块。"""
    return render_failure_block(
        "turn.incomplete",
        view.reason or "The response is incomplete",
        terminal_width=terminal_width,
        measure_width=measure_width,
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
