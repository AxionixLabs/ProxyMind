# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.output import (
    BLOCK_OUTPUT,
    OutputPort
)
from .contracts import (
    PresentationSink,
    PresentationView
)
from .models import (
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
    ToolStartView
)
from .rich import (
    RenderedBlock,
    render_approval_view,
    render_batch_completed_view,
    render_batch_start_view,
    render_failure_view,
    render_generic_tool_result_view,
    render_lifecycle_view,
    render_native_tool_result_view,
    render_plan_steps_start_view,
    render_plan_update_view,
    render_progress_view,
    render_tool_start_view
)


class LegacyPresentationSink(PresentationSink):
    """使用当前终端渲染能力展示结构化数据。"""

    def __init__(self, output: OutputPort) -> None:
        self.output = output

    async def emit(self, view: PresentationView) -> None:
        """使用当前终端适配器展示一项结构化数据。"""
        for rendered in self._render(view):
            if rendered.direct:
                await self.output.print_block(
                    rendered.text,
                    display_parts=(
                        None
                        if rendered.display_parts is None
                        else list(rendered.display_parts)
                    ),
                )
                continue
            await self.output.feed(
                rendered.text,
                display=BLOCK_OUTPUT,
                display_parts=(
                    None
                    if rendered.display_parts is None
                    else list(rendered.display_parts)
                ),
                preserve_display_parts=rendered.preserve_display_parts,
            )

    @staticmethod
    def _render(view: PresentationView) -> tuple[RenderedBlock, ...]:
        """选择与展示数据对应的终端渲染器。"""
        if isinstance(view, (RunStartedView, RunCompletedView)):
            return ()
        if isinstance(view, ApprovalView):
            return (render_approval_view(view),)
        if isinstance(view, ToolStartView):
            return (render_tool_start_view(view),)
        if isinstance(view, GenericToolResultView):
            return (render_generic_tool_result_view(view),)
        if isinstance(view, NativeToolResultView):
            return render_native_tool_result_view(view)
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
