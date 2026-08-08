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
    RunIncompleteView,
    StyledBlock,
    TextSpan,
    ToolStartView
)
from mind_app.presentation.renderers.dispatch import (
    render_presentation_raw_view,
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

_OMITTED_LINES_PATTERN = re.compile(r"(… \+\d+ lines)$")


def _presentation_block_kind(view: PresentationView) -> TuiBlockKind:
    """把共享展示类型映射为 TUI 正文语义。"""
    if isinstance(view, ApprovalView):
        return "approval"
    if isinstance(view, (PlanUpdateView, PlanStepsStartView)):
        return "plan"
    if isinstance(view, _OPERATION_VIEWS):
        return "operation"
    if isinstance(view, (FailureView, LifecycleView, RunIncompleteView)):
        return "notice"

    return "system"


class TuiPresentationSink(PresentationSink):
    """把结构化展示数据写入持久 TUI。"""

    def __init__(self, output: TuiOutputControl) -> None:
        self.output = output

    async def emit(self, view: PresentationView) -> None:
        """渲染并发送一项结构化展示数据。"""
        block_kind     = _presentation_block_kind(view)
        terminal_width = self.output.terminal_width

        blocks = render_presentation_view(
            view,
            terminal_width=terminal_width,
            measure_width=get_cwidth,
        )
        transcript_blocks = render_presentation_transcript_view(
            view,
            terminal_width=terminal_width,
            measure_width=get_cwidth,
        )
        raw_blocks = render_presentation_raw_view(view)

        if not blocks:
            return None
        if len(blocks) != len(transcript_blocks) or len(blocks) != len(raw_blocks):
            raise ValueError("presentation block projections differ in count")

        transcript_key = self.output.runtime.keymap.open_transcript_label

        for block, transcript_block, raw_text in zip(
            blocks,
            transcript_blocks,
            raw_blocks,
            strict=True,
        ):
            await self.output.append_presentation_block(
                _with_transcript_hint(
                    block,
                    transcript_key,
                    terminal_width=terminal_width,
                ),
                block_kind=block_kind,
                transcript_block=transcript_block,
                source=view,
                raw_text=raw_text,
            )


def _with_transcript_hint(
    block: StyledBlock,
    key_label: str,
    *,
    terminal_width: int | None = None
) -> StyledBlock:
    """给 TUI 中的省略行追加完整记录入口提示。"""
    spans: list[TextSpan] = []
    for index, span in enumerate(block.spans):
        text = _OMITTED_LINES_PATTERN.sub(
            lambda match: (
                f"{match.group(1)}"
                f"{_transcript_hint(match.group(1), key_label, terminal_width)}"
            ),
            span.text,
        )
        if (
            text == span.text
            and index >= 2
            and span.text.endswith(" lines")
            and block.spans[index - 1].text.isdigit()
            and block.spans[index - 2].text.endswith("… +")
        ):
            marker = (
                f"{block.spans[index - 2].text}"
                f"{block.spans[index - 1].text}"
                f"{span.text}"
            )
            text = f"{text}{_transcript_hint(marker, key_label, terminal_width)}"
        spans.append(TextSpan(text, span.style, span.hyperlink))

    rendered_spans = tuple(spans)
    if rendered_spans == block.spans:
        return block

    return StyledBlock(
        plain_text=block.plain_text,
        spans=rendered_spans,
        preserve_spans=block.preserve_spans,
        direct=block.direct,
    )


def _transcript_hint(
    marker: str,
    key_label: str,
    terminal_width: int | None
) -> str:
    """返回当前省略行能够完整容纳的记录入口提示。"""
    key = str(key_label or "").strip()
    if not key:
        return ""

    full = f" ({key} to view transcript)"
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return full

    prefix = "  "
    if get_cwidth(f"{prefix}{marker}{full}") <= terminal_width:
        return full

    compact = f" {key}"
    if get_cwidth(f"{prefix}{marker}{compact}") <= terminal_width:
        return compact

    return ""


if __name__ == '__main__':
    pass
