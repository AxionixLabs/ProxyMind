# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from functools import partial
from mind_core.design.terminal_capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities
)
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
    HookRunView,
    LifecycleView,
    NativeToolResultView,
    PatchView,
    PlanStepsStartView,
    PlanUpdateView,
    ProgressView,
    RunCompletedView,
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
from ..core.models import FragmentBlock
from ..rendering.fragments import transcript_hint
from ..core.styles import styled_fragment_block

if typing.TYPE_CHECKING:
    from .output import TuiOutputControl

_OPERATION_VIEWS = (
    ToolStartView,
    GenericToolResultView,
    NativeToolResultView,
    PatchView,
    BatchStartView,
    BatchCompletedView,
    HookRunView,
    ProgressView
)

_WIDTH_AWARE_VIEWS = (
    ToolStartView,
    GenericToolResultView,
    NativeToolResultView,
    PatchView,
    FailureView,
    RunIncompleteView
)

_WORK_COMPLETED_VIEWS = (
    NativeToolResultView,
    BatchCompletedView
)

_NON_WORK_COMPLETED_TOOL_NAMES = frozenset({"view_image"})

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
                f"{transcript_hint(match.group(1), key_label, terminal_width)}"
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
            text = f"{text}{transcript_hint(marker, key_label, terminal_width)}"
        spans.append(TextSpan(text, span.style, span.hyperlink))

    rendered_spans = tuple(spans)
    if rendered_spans == block.spans:
        return block

    return StyledBlock(
        plain_text=block.plain_text,
        spans=rendered_spans,
        line_fill_styles=block.line_fill_styles,
        preserve_spans=block.preserve_spans,
        direct=block.direct,
    )


def render_presentation_fragment_block(
    view: PresentationView,
    block_index: int,
    terminal_width: int,
    *,
    block_count: int,
    key_label: str = "",
    hyperlinks: bool = False,
    terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES
) -> FragmentBlock:
    """按指定宽度生成一项结构化展示片段。"""
    blocks = render_presentation_view(
        view,
        terminal_width=terminal_width,
        measure_width=get_cwidth,
        terminal_capabilities=terminal_capabilities,
    )
    if len(blocks) != block_count:
        raise ValueError("presentation display block count changed")

    block = _with_transcript_hint(
        blocks[block_index],
        key_label,
        terminal_width=terminal_width,
    )
    return styled_fragment_block(
        block,
        hyperlinks=hyperlinks,
    )


class TuiPresentationSink(PresentationSink):
    """把结构化展示数据写入持久 TUI。"""

    def __init__(self, output: "TuiOutputControl") -> None:
        self.output = output
        self._active_patch_call_id: str | None = None

    def _patch_call_matches(self, call_id: str) -> bool:
        """判断结果是否属于当前活动补丁。"""
        return call_id == self._active_patch_call_id

    def _commit_active_patch(self) -> None:
        """在新调用开始前稳定提交当前补丁 cell。"""
        document = self.output.runtime.document
        if document.active_block is not None and document.active_kind == "operation":
            self.output.runtime.commit_active_renderable(
                document.active_block,
                transcript_block=document.active_transcript_block,
                source=document.active_source,
                raw_text=document.active_raw_text,
                display_renderer=document.active_display_renderer,
                display_render_width=document.active_display_render_width,
            )
        self._active_patch_call_id = None

    async def _emit_patch(self, view: PatchView) -> None:
        """按调用身份原位更新并提交一个补丁 cell。"""
        terminal_width = self.output.terminal_width

        block = render_presentation_view(
            view,
            terminal_width=terminal_width,
            measure_width=get_cwidth,
            terminal_capabilities=self.output.runtime.terminal_capabilities,
        )[0]

        transcript_block = render_presentation_transcript_view(
            view,
            terminal_width=terminal_width,
            measure_width=get_cwidth,
            terminal_capabilities=self.output.runtime.terminal_capabilities,
        )[0]

        raw_text = render_presentation_raw_view(view)[0]

        display_renderer = partial(
            render_presentation_fragment_block,
            view,
            0,
            block_count=1,
            key_label=self.output.runtime.keymap.open_transcript_label,
            hyperlinks=self.output.runtime.hyperlinks_enabled,
            terminal_capabilities=self.output.runtime.terminal_capabilities,
        )

        display_fragment = styled_fragment_block(
            block,
            hyperlinks=self.output.runtime.hyperlinks_enabled,
        )

        transcript_fragment = styled_fragment_block(
            transcript_block,
            hyperlinks=self.output.runtime.hyperlinks_enabled,
        )

        if view.phase == "applying":
            await self.output.prepare_active_presentation()
            if self._active_patch_call_id is not None:
                if self._patch_call_matches(view.call_id):
                    self.output.runtime.set_active_renderable(
                        display_fragment,
                        kind="operation",
                        transcript_block=transcript_fragment,
                        source=view,
                        raw_text=raw_text,
                        display_renderer=display_renderer,
                        display_render_width=terminal_width,
                    )
                    return None
                self._commit_active_patch()

            if self.output.runtime.document.active_block is not None:
                return None

            self.output.runtime.set_active_renderable(
                display_fragment,
                kind="operation",
                transcript_block=transcript_fragment,
                source=view,
                raw_text=raw_text,
                display_renderer=display_renderer,
                display_render_width=terminal_width,
            )
            self._active_patch_call_id = view.call_id
            return None

        if self._patch_call_matches(view.call_id):
            self.output.record_presentation_block(block.plain_text)
            self.output.runtime.commit_active_renderable(
                display_fragment,
                transcript_block=transcript_fragment,
                source=view,
                raw_text=raw_text,
                display_renderer=display_renderer,
                display_render_width=terminal_width,
            )
            self._active_patch_call_id = None
        else:
            await self.output.append_presentation_block(
                block,
                block_kind="operation",
                transcript_block=transcript_block,
                source=view,
                raw_text=raw_text,
                display_renderer=display_renderer,
                display_render_width=terminal_width,
            )
        self.output.note_work_activity()

    async def emit(self, view: PresentationView) -> None:
        """渲染并发送一项结构化展示数据。"""
        if isinstance(view, RunCompletedView):
            if self._active_patch_call_id is not None:
                self._commit_active_patch()
            await self.output.complete_turn()
            return None
        if isinstance(view, PatchView):
            await self._emit_patch(view)
            return None

        block_kind     = _presentation_block_kind(view)
        terminal_width = self.output.terminal_width

        blocks = render_presentation_view(
            view,
            terminal_width=terminal_width,
            measure_width=get_cwidth,
            terminal_capabilities=self.output.runtime.terminal_capabilities,
        )
        transcript_blocks = render_presentation_transcript_view(
            view,
            terminal_width=terminal_width,
            measure_width=get_cwidth,
            terminal_capabilities=self.output.runtime.terminal_capabilities,
        )
        raw_blocks = render_presentation_raw_view(view)

        if not blocks:
            return None
        if len(blocks) != len(transcript_blocks) or len(blocks) != len(raw_blocks):
            raise ValueError("presentation block projections differ in count")

        transcript_key = self.output.runtime.keymap.open_transcript_label
        width_aware    = isinstance(view, _WIDTH_AWARE_VIEWS)

        for index, (block, transcript_block, raw_text) in enumerate(zip(
            blocks, transcript_blocks, raw_blocks, strict=True,
        )):
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
                display_renderer=(
                    partial(
                        render_presentation_fragment_block,
                        view,
                        index,
                        block_count=len(blocks),
                        key_label=transcript_key,
                        hyperlinks=self.output.runtime.hyperlinks_enabled,
                        terminal_capabilities=self.output.runtime.terminal_capabilities,
                    )
                    if width_aware
                    else None
                ),
                display_render_width=(
                    terminal_width if width_aware else None
                ),
            )

        if isinstance(view, _WORK_COMPLETED_VIEWS) or (
            isinstance(view, GenericToolResultView)
            and view.name not in _NON_WORK_COMPLETED_TOOL_NAMES
        ):
            self.output.note_work_activity()


if __name__ == '__main__':
    pass
