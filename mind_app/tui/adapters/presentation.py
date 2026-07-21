# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.presentation.contracts import (
    PresentationSink,
    PresentationView
)
from mind_app.presentation.models import (
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
    ToolStartView,
)
from mind_app.presentation.renderers.dispatch import render_presentation_view
from prompt_toolkit.utils import get_cwidth
from ..core.document import TuiBlockKind
from .output import TuiOutputControl


class TuiPresentationSink(PresentationSink):
    """把结构化展示数据写入持久 TUI。"""

    def __init__(self, output: TuiOutputControl) -> None:
        self.output = output

    async def emit(self, view: PresentationView) -> None:
        """渲染并发送一项结构化展示数据。"""
        block_kind = _presentation_block_kind(view)
        blocks = render_presentation_view(
            view,
            terminal_width=self.output.terminal_width,
            measure_width=get_cwidth,
        )
        if not blocks:
            return None
        if isinstance(view, (ToolStartView, NativeToolResultView)):
            self.output.runtime.append_gap()
        for block in blocks:
            await self.output.append_presentation_block(
                block,
                block_kind=block_kind,
            )


_OPERATION_VIEWS = (
    ToolStartView,
    GenericToolResultView,
    NativeToolResultView,
    PlanUpdateView,
    PlanStepsStartView,
    BatchStartView,
    BatchCompletedView,
    ProgressView,
)


def _presentation_block_kind(view: PresentationView) -> TuiBlockKind:
    """把共享展示类型映射为 TUI 正文语义。"""
    if isinstance(view, ApprovalView):
        return "approval"
    if isinstance(view, _OPERATION_VIEWS):
        return "operation"
    if isinstance(view, (FailureView, LifecycleView)):
        return "notice"
    return "system"


if __name__ == '__main__':
    pass
