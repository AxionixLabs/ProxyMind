# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from ..contracts import PresentationView
from ..models import (
    ApprovalView,
    BatchCompletedView,
    BatchStartView,
    FailureView,
    GenericToolResultView,
    LifecycleView,
    NativeToolResultView,
    PlanStepsStartView,
    PlanUpdateView,
    ProgressView,
    RunCompletedView,
    RunStartedView,
    StyledBlock,
    ToolStartView,
)
from .approval import render_approval_view
from .batch import render_batch_completed_view, render_batch_start_view
from .lifecycle import render_failure_view, render_lifecycle_view
from .plan import render_plan_steps_start_view, render_plan_update_view
from .progress import render_progress_view
from .tool import (
    render_generic_tool_result_view,
    render_native_tool_result_view,
    render_tool_start_view,
)


def render_presentation_view(
    view: PresentationView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> tuple[StyledBlock, ...]:
    """选择结构化展示数据对应的共享渲染器。"""
    if isinstance(view, (RunStartedView, RunCompletedView)):
        return ()
    if isinstance(view, ApprovalView):
        return (render_approval_view(view),)
    if isinstance(view, ToolStartView):
        return (render_tool_start_view(view),)
    if isinstance(view, GenericToolResultView):
        return (render_generic_tool_result_view(view),)
    if isinstance(view, NativeToolResultView):
        return render_native_tool_result_view(
            view,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
    if isinstance(view, PlanUpdateView):
        return (render_plan_update_view(view),)
    if isinstance(view, PlanStepsStartView):
        return (render_plan_steps_start_view(view),)
    if isinstance(view, BatchStartView):
        return (render_batch_start_view(view),)
    if isinstance(view, BatchCompletedView):
        return (render_batch_completed_view(view),)
    if isinstance(view, FailureView):
        return (render_failure_view(view),)
    if isinstance(view, LifecycleView):
        return (render_lifecycle_view(view),)
    if isinstance(view, ProgressView):
        return (render_progress_view(view),)
    raise TypeError(f"Unsupported presentation view: {type(view).__name__}")


if __name__ == '__main__':
    pass
