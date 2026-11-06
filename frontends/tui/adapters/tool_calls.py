# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field,
    replace,
)
from functools import partial

from prompt_toolkit.utils import get_cwidth

from agent.application.views.tools import (
    GenericToolResultView,
    ToolStartView,
)
from agent.ports.presentation import StyledBlock
from frontends.terminal.renderers.call import (
    ToolCallSnapshot,
    call_raw_text,
    call_transcript,
    render_call_snapshot,
)
from frontends.tui.core.document import (
    TranscriptBlock,
    WidthBlockRenderer,
)
from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.status_frames import spinner_indicator_fragment
from frontends.tui.core.styles import styled_fragment_block
from frontends.tui.rendering.fragments import with_transcript_hint

if typing.TYPE_CHECKING:
    from .output import TuiOutputControl


def render_call_fragment(snapshot: ToolCallSnapshot, width: int, *, key_label: str = "") -> FragmentBlock:
    """按当前宽度重建单笔调用的显示摘要。"""
    block = render_call_snapshot(
        snapshot, terminal_width=width, measure_width=get_cwidth,
    )
    if key_label:
        block = with_transcript_hint(block, key_label, terminal_width=width)
    return styled_fragment_block(block)


@dataclass(slots=True)
class _CallEntry:
    """持有一笔调用的展示快照和原位提交目标。"""

    snapshot: ToolCallSnapshot
    key_label: str
    target: TranscriptBlock | None = None
    transcript: FragmentBlock = field(init=False)
    raw: str = field(init=False)
    renderer: WidthBlockRenderer = field(init=False)
    width: int = -1
    display: FragmentBlock | None = None

    def __post_init__(self) -> None:
        """缓存不随动画帧变化的完整载荷。"""
        self.update(self.snapshot)

    def update(self, snapshot: ToolCallSnapshot) -> None:
        """只在调用事实变化时序列化完整记录。"""
        self.snapshot = snapshot
        self.transcript = styled_fragment_block(StyledBlock(plain_text=call_transcript(snapshot)))
        self.raw = call_raw_text(snapshot)
        self.renderer = partial(render_call_fragment, snapshot, key_label=self.key_label)
        self.width = -1
        self.display = None

    def render_at(self, width: int) -> FragmentBlock:
        """缓存当前宽度的静态摘要，动画帧仅替换指示符。"""
        if self.display is None or self.width != width:
            self.display = self.renderer(width)
            self.width = width
        return self.display


class ToolCallPresentation:
    """在一个 OutputSession 内按调用身份更新普通工具记录。

    动态尾部只属于当前调用；正文交接后保留稳定目标以接收迟到结果。
    本类只持有展示投影，动画和真实终态均由既有活动所有者提供。
    """

    def __init__(self, output: "TuiOutputControl") -> None:
        """绑定输出所有者并创建仅限当前回合的调用索引。"""
        self.output = output
        self._calls: dict[str, _CallEntry] = {}
        self._active: _CallEntry | None = None
        self._last_frame: FragmentBlock | None = None
        self._terminal = False

    async def emit(self, view: ToolStartView | GenericToolResultView) -> None:
        """配对正式调用身份；重复事件不重开已经完成的记录。"""
        if not view.call_id:
            raise ValueError("inline tool presentation requires call_id")
        if self._terminal:
            return
        entry = self._calls.get(view.call_id)
        if entry is not None:
            if entry.snapshot.view.name != view.name:
                raise ValueError("tool presentation identity was reused")
            if not entry.snapshot.pending or isinstance(view, ToolStartView):
                return
            if not view.arguments:
                view = replace(view, arguments=dict(entry.snapshot.view.arguments))
            entry.update(ToolCallSnapshot(view))
        else:
            await self.output.prepare_active_presentation()
            if self._terminal:
                return
            entry = _CallEntry(ToolCallSnapshot(view), self.output.runtime.keymap.open_transcript_label)
            self._calls[view.call_id] = entry
            self._active = entry
        self.output.record_presentation_block(call_transcript(ToolCallSnapshot(view)))
        self._last_frame = None
        if isinstance(view, GenericToolResultView):
            self._commit(entry)
            self.output.note_work_activity()
        else:
            self.render(None)
        self.output.runtime.activity.bind_tool_renderer(self)

    def render(self, phase: float | None, *, tool_id: str = "", reviewing: bool = False) -> bool:
        """只在调用仍待结果时消费当前前景帧；暂停时保持静态标记。"""
        entry = self._active
        runtime = self.output.runtime
        if runtime.turn_output_suppressed:
            return False
        if entry is None:
            completed = self._calls.get(tool_id)
            return completed is not None and not completed.snapshot.pending and phase is not None
        if not entry.snapshot.pending:
            return False
        if runtime.document.active_block is not None and runtime.document.active_source is not entry.snapshot.view:
            return False
        block = entry.render_at(self.output.terminal_width)
        owns_activity = tool_id == entry.snapshot.view.call_id
        if owns_activity and phase is not None and self.output.animate and self.output.terminal_width >= 4:
            fragments = list(block.fragments)
            style, symbol = spinner_indicator_fragment(phase, family="wait", color_level=runtime.activity.color_level)
            if fragments and fragments[0][1] == "•":
                fragments[0] = (style, symbol)
            block = FragmentBlock(tuple(fragments))
        if block != self._last_frame:
            runtime.set_active_renderable(
                block, kind="operation", source=entry.snapshot.view,
                transcript_block=entry.transcript,
                raw_text=entry.raw, display_renderer=entry.renderer,
                display_render_width=self.output.terminal_width,
            )
            self._last_frame = block
        return owns_activity

    def _commit(self, entry: _CallEntry) -> None:
        """以同一视觉事务更新活动尾部或已经提交的原始位置。"""
        runtime = self.output.runtime
        snapshot = entry.snapshot
        block = entry.render_at(self.output.terminal_width)
        if entry.target is not None:
            runtime.replace_stable_renderable(
                entry.target, block, transcript_block=entry.transcript, source=snapshot.view,
                raw_text=entry.raw, display_renderer=entry.renderer,
                display_render_width=self.output.terminal_width,
                transcript_stable=not snapshot.pending,
            )
        elif self._active is entry:
            if runtime.document.active_block is None:
                runtime.set_active_renderable(block, kind="operation", source=snapshot.view)
            context = self.output.surface_context
            entry.target = runtime.commit_active_renderable(
                block, transcript_block=entry.transcript, source=snapshot.view,
                raw_text=entry.raw, display_renderer=entry.renderer,
                display_render_width=self.output.terminal_width,
                stable_id=f"tool:{context.surface_id if context is not None else ''}:{snapshot.view.call_id}",
                transcript_stable=not snapshot.pending,
            )
        if self._active is entry:
            self._active = None
            self._last_frame = None

    def suspend(self) -> None:
        """让出动态尾部，保留未完成调用的原位更新目标。"""
        if self._active is not None:
            self._commit(self._active)

    def finish(self, status: str = "") -> None:
        """收束当前会话；没有结果的调用仅标记观察结束。"""
        self._terminal = True
        with self.output.runtime.screen.visual_update():
            for entry in self._calls.values():
                if entry.snapshot.pending:
                    entry.update(replace(
                        entry.snapshot,
                        observation_ended="interrupted" if status in {"interrupted", "cancelled"} else "unconfirmed",
                    ))
                    self._commit(entry)
        self.output.runtime.activity.unbind_tool_renderer(self)


if __name__ == '__main__':
    pass
