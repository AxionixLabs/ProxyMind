# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
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
    StyledBlock,
    TextSpan,
    ToolStartView
)
from mind_app.presentation.renderers.dispatch import (
    render_presentation_transcript_view,
    render_presentation_view
)
from prompt_toolkit.utils import get_cwidth
from ..core.document import TuiBlockKind
from .output import TuiOutputControl

_OPERATION_VIEWS = (
    ToolStartView,
    GenericToolResultView,
    NativeToolResultView,
    BatchStartView,
    BatchCompletedView,
    ProgressView
)

_OMITTED_LINES_PATTERN = re.compile(r"(… \+\d+ lines)(?! \(ctrl \+ t)")
_TRANSCRIPT_HINT       = " (ctrl + t to view transcript)"


def _presentation_block_kind(view: PresentationView) -> TuiBlockKind:
    """把共享展示类型映射为 TUI 正文语义。"""
    if isinstance(view, ApprovalView):
        return "approval"
    if isinstance(view, (PlanUpdateView, PlanStepsStartView)):
        return "plan"
    if isinstance(view, _OPERATION_VIEWS):
        return "operation"
    if isinstance(view, (FailureView, LifecycleView)):
        return "notice"

    return "system"


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
        transcript_blocks = render_presentation_transcript_view(
            view,
            terminal_width=self.output.terminal_width,
            measure_width=get_cwidth,
        )

        if not blocks:
            return None
        if len(blocks) != len(transcript_blocks):
            raise ValueError("presentation display and transcript block counts differ")

        for block, transcript_block in zip(blocks, transcript_blocks):
            await self.output.append_presentation_block(
                _with_transcript_hint(block),
                block_kind=block_kind,
                transcript_block=transcript_block,
            )


def _with_transcript_hint(block: StyledBlock) -> StyledBlock:
    """给 TUI 中的省略行追加完整记录入口提示。"""
    spans: list[TextSpan] = []
    for index, span in enumerate(block.spans):
        text = _OMITTED_LINES_PATTERN.sub(
            rf"\1{_TRANSCRIPT_HINT}",
            span.text,
        )
        if (
            text == span.text
            and index >= 2
            and span.text.endswith(" lines")
            and block.spans[index - 1].text.isdigit()
            and block.spans[index - 2].text.endswith("… +")
        ):
            text = f"{text}{_TRANSCRIPT_HINT}"
        spans.append(TextSpan(text, span.style))

    rendered_spans = tuple(spans)
    if rendered_spans == block.spans:
        return block

    return StyledBlock(
        plain_text=block.plain_text,
        spans=rendered_spans,
        preserve_spans=block.preserve_spans,
        direct=block.direct,
    )


if __name__ == '__main__':
    pass
