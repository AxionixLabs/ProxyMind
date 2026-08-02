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
    ToolStartView
)
from .approval import render_approval_view
from .batch import (
    render_batch_completed_view,
    render_batch_start_transcript_view,
    render_batch_start_view
)
from .lifecycle import (
    render_failure_view,
    render_lifecycle_view
)
from .plan import (
    render_plan_steps_start_view,
    render_plan_update_view
)
from .progress import render_progress_view
from .tool import (
    render_generic_tool_result_raw_text,
    render_generic_tool_result_transcript_view,
    render_generic_tool_result_view,
    render_native_tool_result_raw_text,
    render_native_tool_result_transcript_view,
    render_native_tool_result_view,
    render_tool_start_raw_text,
    render_tool_start_transcript_view,
    render_tool_start_view
)
from ..terminal_text import (
    sanitize_styled_block,
    sanitize_terminal_text
)


def render_presentation_view(
    view: PresentationView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> tuple[StyledBlock, ...]:
    """选择结构化展示数据对应的共享渲染器。"""
    blocks = _render_presentation_view(
        view,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    return tuple(
        sanitize_styled_block(block, measure_width=measure_width)
        for block in blocks
    )


def render_presentation_transcript_view(
    view: PresentationView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> tuple[StyledBlock, ...]:
    """把结构化展示数据转换为不省略原始内容的记录块。"""
    _ = terminal_width

    if isinstance(view, ToolStartView):
        blocks = (render_tool_start_transcript_view(view),)
    elif isinstance(view, GenericToolResultView):
        blocks = (render_generic_tool_result_transcript_view(view),)
    elif isinstance(view, NativeToolResultView):
        blocks = render_native_tool_result_transcript_view(view)
    elif isinstance(view, BatchStartView):
        blocks = (render_batch_start_transcript_view(view),)
    else:
        # Transcript 保存逻辑内容，折行只由具体前端在显示时决定。
        blocks = _render_presentation_view(
            view,
            terminal_width=None,
            measure_width=None,
        )

    return tuple(
        sanitize_styled_block(block, measure_width=measure_width)
        for block in blocks
    )


def render_presentation_raw_view(
    view: PresentationView
) -> tuple[str, ...]:
    """把结构化展示数据转换为不含视觉装饰的文本块。"""
    if isinstance(view, ToolStartView):
        values = (render_tool_start_raw_text(view),)
    elif isinstance(view, GenericToolResultView):
        values = (render_generic_tool_result_raw_text(view),)
    elif isinstance(view, NativeToolResultView):
        values = render_native_tool_result_raw_text(view)
    else:
        values = tuple(
            block.plain_text
            for block in render_presentation_transcript_view(view)
        )

    return tuple(
        sanitize_terminal_text(value).strip("\n")
        for value in values
    )


def _render_presentation_view(
    view: PresentationView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> tuple[StyledBlock, ...]:
    """把展示视图转换为尚未执行终端清理的文本块。"""
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
        return (render_failure_view(
            view,
            terminal_width=terminal_width,
            measure_width=measure_width,
        ),)
    if isinstance(view, LifecycleView):
        return (render_lifecycle_view(view),)
    if isinstance(view, ProgressView):
        return (render_progress_view(view),)

    raise TypeError(f"Unsupported presentation view: {type(view).__name__}")


if __name__ == '__main__':
    pass
