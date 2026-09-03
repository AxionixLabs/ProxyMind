# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from functools import partial

from prompt_toolkit.utils import get_cwidth

from agent.application.views import (
    ApprovalReviewView,
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
    ToolStartView,
)
from agent.application.views.contracts import (
    PresentationSink,
    PresentationView,
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
)
from frontends.terminal.capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities,
)
from frontends.terminal.renderers.dispatch import (
    render_presentation_raw_view,
    render_presentation_transcript_view,
    render_presentation_view,
)
from ..core.document import TuiBlockKind
from ..core.models import FragmentBlock
from ..core.styles import styled_fragment_block
from ..rendering.fragments import transcript_hint

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
    BatchStartView,
    BatchCompletedView,
    PlanUpdateView,
    PlanStepsStartView,
    HookRunView,
    FailureView,
    RunIncompleteView
)

_WORK_COMPLETED_VIEWS = (
    NativeToolResultView,
    BatchCompletedView
)

_NON_WORK_COMPLETED_TOOL_NAMES = frozenset({"view_image"})

_OMITTED_LINES_PATTERN = re.compile(r"(… \+\d+ lines)(?=\n|$)")


def _presentation_block_kind(view: PresentationView) -> TuiBlockKind:
    """把共享展示类型映射为 TUI 正文语义。"""
    if isinstance(view, (ApprovalView, ApprovalReviewView)):
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
                match.group(1)
                + transcript_hint(
                match.group(1),
                key_label,
                terminal_width,
                prefix="    ",
            )
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
            marker_prefix = block.spans[index - 2].text.rsplit("\n", 1)[-1]
            marker = (
                f"{marker_prefix.lstrip()}"
                f"{block.spans[index - 1].text}"
                f"{span.text}"
            )
            text = text + transcript_hint(
                marker,
                key_label,
                terminal_width,
                prefix="    ",
            )
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
        self._stable_patch_call_ids: set[str] = set()
        self._pending_terminal_waits: dict[str, list[NativeToolResultView]] = {}
        self._active_terminal_waits: set[tuple[str, str]] = set()

    @staticmethod
    def _native_payload(view: NativeToolResultView) -> dict[str, typing.Any]:
        """提取原生终端结果中的结构化数据。"""
        data = view.data
        if not isinstance(data, dict):
            return {}

        results = data.get("results")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                item_data = item.get("data")
                if isinstance(item_data, dict):
                    return item_data
        return data

    @classmethod
    def _terminal_session_id(cls, view: NativeToolResultView) -> str:
        """返回终端结果关联的会话标识。"""
        payload = cls._native_payload(view)
        return str(
            payload.get("session_id")
            or view.arguments.get("session_id")
            or ""
        ).strip()

    @classmethod
    def _terminal_stdin(cls, view: NativeToolResultView) -> str:
        """返回本次终端交互写入的原始输入。"""
        return str(view.arguments.get("stdin") or "")

    @classmethod
    def _terminal_control(cls, view: NativeToolResultView) -> str:
        """返回本次终端交互使用的控制动作。"""
        payload = cls._native_payload(view)
        return str(
            view.arguments.get("control")
            or payload.get("control")
            or "none"
        ).strip().lower()

    @classmethod
    def _terminal_status(cls, view: NativeToolResultView) -> str:
        """返回终端结果的生命周期状态。"""
        return str(cls._native_payload(view).get("status") or "").strip().lower()

    @classmethod
    def _terminal_command(cls, view: NativeToolResultView) -> str:
        """返回终端等待状态使用的命令摘要。"""
        payload = cls._native_payload(view)
        return str(
            payload.get("command")
            or view.arguments.get("command")
            or view.arguments.get("session_id")
            or ""
        ).strip()

    @classmethod
    def _is_running_exec_start(cls, view: NativeToolResultView) -> bool:
        """判断结果是否只是后台终端的启动确认。"""
        return (
            view.name == "exec_command"
            and cls._terminal_status(view) == "running"
        )

    @classmethod
    def _is_empty_terminal_wait(cls, view: NativeToolResultView) -> bool:
        """判断结果是否为空输入的后台终端轮询。"""
        return (
            view.name == "write_stdin"
            and not cls._terminal_stdin(view)
            and cls._terminal_control(view) == "none"
        )

    async def _flush_terminal_wait(self, session_id: str) -> None:
        """提交指定会话合并后的等待记录。"""
        views = self._pending_terminal_waits.pop(session_id, None)
        if views:
            completed: set[tuple[str, str]] = set()
            for view in views:
                identity = (view.call_id, session_id)
                if (
                    identity not in self._active_terminal_waits
                    or identity in completed
                ):
                    continue
                await self.output.complete_terminal_wait(
                    call_id=view.call_id,
                    session_id=session_id,
                    command=self._terminal_command(view),
                )
                self._active_terminal_waits.discard(identity)
                completed.add(identity)
            await self._emit_view(views[-1])

    async def _flush_all_terminal_waits(self) -> None:
        """在新的展示单元开始前提交所有等待记录。"""
        session_ids = tuple(self._pending_terminal_waits)
        for session_id in session_ids:
            await self._flush_terminal_wait(session_id)

    async def _emit_patch(self, view: PatchView) -> None:
        """按补丁生命周期提交稳定单元和失败单元。"""
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

        if view.phase == "proposed":
            if view.call_id in self._stable_patch_call_ids:
                return None
            await self.output.append_presentation_block(
                block,
                block_kind="operation",
                transcript_block=transcript_block,
                source=view,
                raw_text=raw_text,
                display_renderer=display_renderer,
                display_render_width=terminal_width,
            )
            self._stable_patch_call_ids.add(view.call_id)
            self.output.note_work_activity()
            return None

        if view.phase == "applied" and view.call_id in self._stable_patch_call_ids:
            self._stable_patch_call_ids.discard(view.call_id)
            self.output.note_work_activity()
            return None

        if view.phase == "failed" and view.call_id in self._stable_patch_call_ids:
            self._stable_patch_call_ids.discard(view.call_id)
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
            return None

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

    async def _emit_view(self, view: PresentationView) -> None:
        """渲染并发送不需要生命周期归并的一项展示数据。"""
        if isinstance(view, RunCompletedView):
            self._stable_patch_call_ids.clear()
            await self.output.complete_turn()
            return None
        if isinstance(view, (RunIncompleteView, FailureView)):
            self._stable_patch_call_ids.clear()
        if isinstance(view, PatchView):
            await self._emit_patch(view)
            return None

        block_kind = _presentation_block_kind(view)
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
        width_aware = isinstance(view, _WIDTH_AWARE_VIEWS)

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

    async def emit(self, view: PresentationView) -> None:
        """渲染并发送一项结构化展示数据。"""
        if isinstance(view, NativeToolResultView):
            if self._is_running_exec_start(view):
                return None

            if self._is_empty_terminal_wait(view):
                session_id = self._terminal_session_id(view)
                if (
                    not self.output.runtime.execution_active
                    or not session_id
                    or self._terminal_status(view) not in {"running", "exited"}
                ):
                    return None
                for pending_session_id in tuple(self._pending_terminal_waits):
                    if pending_session_id != session_id:
                        await self._flush_terminal_wait(pending_session_id)
                self._pending_terminal_waits.setdefault(session_id, []).append(view)
                identity = (view.call_id, session_id)
                if (
                    self._terminal_status(view) == "running"
                    and identity not in self._active_terminal_waits
                ):
                    await self.output.start_terminal_wait(
                        call_id=view.call_id,
                        session_id=session_id,
                        command=self._terminal_command(view),
                    )
                    self._active_terminal_waits.add(identity)
                if self._terminal_status(view) == "exited":
                    await self._flush_terminal_wait(session_id)
                return None

            if view.name == "write_stdin":
                session_id = self._terminal_session_id(view)
                if session_id:
                    await self._flush_terminal_wait(session_id)
            else:
                await self._flush_all_terminal_waits()
        else:
            await self._flush_all_terminal_waits()

        await self._emit_view(view)

    async def flush_terminal_waits_before_assistant_output(self) -> None:
        """在助手正文开始前提交后台终端等待记录。"""
        await self._flush_all_terminal_waits()


if __name__ == '__main__':
    pass
