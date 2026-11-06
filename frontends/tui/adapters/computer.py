# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    replace,
)
from functools import partial

from prompt_toolkit.utils import get_cwidth

from agent.application.views.approval import (
    ApprovalReviewView,
    ApprovalView,
)
from agent.application.views.tools import (
    NativeToolResultView,
    ToolStartView,
)
from agent.ports.presentation import StyledBlock
from frontends.terminal.renderers.computer import (
    ComputerAction,
    computer_action,
    render_computer_activity,
)
from frontends.terminal.renderers.dispatch import (
    render_presentation_raw_view,
    render_presentation_transcript_view,
)
from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.document import (
    TranscriptBlock,
    WidthBlockRenderer,
)
from frontends.tui.core.status_frames import spinner_indicator_fragment
from frontends.tui.core.styles import styled_fragment_block

if typing.TYPE_CHECKING:
    from .output import TuiOutputControl


def render_computer_fragment(
    actions: tuple[ComputerAction, ...],
    finished: bool,
    key_label: str,
    width: int,
    *,
    reviewing: bool = False,
) -> FragmentBlock:
    """按当前终端宽度渲染不可变桌面操作快照。"""
    return styled_fragment_block(render_computer_activity(
        actions, finished=finished, terminal_width=width,
        measure_width=get_cwidth, key_label=key_label, reviewing=reviewing,
    ))


@dataclass(slots=True)
class _CommittedComputerGroup:
    """持有已让出动态尾部且仍等待结果的分组，结果只能原位更新其稳定记录。"""

    target: TranscriptBlock
    actions: tuple[ComputerAction, ...]
    transcript_parts: list[str]
    raw_parts: list[str]


def _finished_actions(actions: tuple[ComputerAction, ...], status: str) -> tuple[ComputerAction, ...]:
    """仅为缺少结果的动作标记观察结束，保留真实执行结果。"""
    return tuple(
        replace(action, unresolved="interrupted" if status in {"interrupted", "cancelled"} else "unconfirmed")
        if action.pending else action for action in actions
    )


class ComputerPresentation:
    """在单个输出会话内归并相邻桌面调用，复用正文尾部和活动时钟。

    开始与结果均来自正式展示视图；正文、其他展示或关闭提交当前组。
    本类不执行桌面动作，也不把停止观察解释为远端调用已经取消。
    """

    def __init__(self, output: "TuiOutputControl") -> None:
        """绑定唯一输出所有者，首次桌面事件才占用动态尾部。"""
        self.output = output
        self._actions: tuple[ComputerAction, ...] = ()
        self._transcript_parts: list[str] = []
        self._raw_parts: list[str] = []
        self._source: ToolStartView | NativeToolResultView | None = None
        self._transcript = FragmentBlock(())
        self._raw = ""
        self._seen: set[tuple[str, bool]] = set()
        self._renderer: WidthBlockRenderer | None = None
        self._last_frame: FragmentBlock | None = None
        self._reviewing_renderer: WidthBlockRenderer | None = None
        self._terminal = False
        self._pending_groups: dict[str, _CommittedComputerGroup] = {}

    async def emit(self, view: ToolStartView | NativeToolResultView) -> None:
        """由展示入口串行派发；已提交分组的迟到结果不重新取得动态尾部。"""
        if self._terminal:
            return
        completed = isinstance(view, NativeToolResultView)
        key = (view.call_id, completed)
        if view.call_id and key in self._seen:
            return
        if view.call_id and not completed and (view.call_id, True) in self._seen:
            return
        previous = self._pending_groups.get(view.call_id)
        if previous is not None and isinstance(view, NativeToolResultView):
            action = computer_action(view)
            previous.actions = tuple(action if item.call_id == view.call_id else item for item in previous.actions)
            text, raw = self._record_parts(view)
            previous.transcript_parts.append(text)
            previous.raw_parts.append(raw)
            self._replace_group(previous)
            self._pending_groups.pop(view.call_id)
            self._seen.add(key)
            self.output.note_work_activity()
            return
        if not self._actions:
            await self.output.prepare_active_presentation()
            if self._terminal:
                return
            self._source = view
        action = computer_action(view)
        index = next((i for i, item in enumerate(self._actions)
                      if view.call_id and item.call_id == view.call_id), None)
        if index is None:
            self._actions = (*self._actions, action)
        elif completed:
            self._actions = (*self._actions[:index], action, *self._actions[index + 1:])
        if view.call_id:
            self._seen.add(key)
        self._record_view(view)
        self._renderer = partial(render_computer_fragment, self._actions, False, "")
        self._reviewing_renderer = partial(render_computer_fragment, self._actions, False, "", reviewing=True)
        self.render(None)
        self.output.runtime.activity.bind_tool_renderer(self)
        if completed:
            self.output.note_work_activity()

    def retain_approval(self, view: ApprovalView | ApprovalReviewView) -> bool:
        """把当前桌面调用的审批结果保留在组内完整记录，避免拆成两个操作。"""
        call_id = view.approval.get("call_id") if isinstance(view, ApprovalView) else view.call_id
        if not isinstance(call_id, str) or not any(action.call_id == call_id for action in self._actions):
            return False
        self._record_view(view)
        self.render(None)
        return True

    def _record_view(self, view: ToolStartView | NativeToolResultView | ApprovalView | ApprovalReviewView) -> None:
        """只序列化新事件，保留完整记录；动画帧不重复处理控件树和参数。"""
        text, raw = self._record_parts(view)
        self._transcript_parts.append(text)
        self._raw_parts.append(raw)
        self._transcript = styled_fragment_block(StyledBlock(plain_text="\n\n".join(self._transcript_parts)))
        self._raw = "\n\n".join(self._raw_parts)
        self._last_frame = None

    def _record_parts(self, view: ToolStartView | NativeToolResultView | ApprovalView | ApprovalReviewView) -> tuple[str, str]:
        """记录一次正式展示事件，返回完整记录和复制文本。"""
        text = "\n\n".join(block.plain_text for block in render_presentation_transcript_view(view))
        raw = "\n\n".join(render_presentation_raw_view(view))
        self.output.record_presentation_block(text)
        return text, raw

    def _replace_group(self, group: _CommittedComputerGroup, status: str = "") -> None:
        """更新已提交分组的结果，保留当前动态尾部和既有记录位置。"""
        renderer = partial(
            render_computer_fragment, _finished_actions(group.actions, status),
            True, self.output.runtime.keymap.open_transcript_label,
        )
        self.output.runtime.replace_stable_renderable(
            group.target, renderer(self.output.terminal_width),
            transcript_block=styled_fragment_block(StyledBlock(plain_text="\n\n".join(group.transcript_parts))),
            raw_text="\n\n".join(group.raw_parts), display_renderer=renderer,
            display_render_width=self.output.terminal_width,
            transcript_stable=self._terminal or not any(action.pending and action.call_id for action in group.actions),
        )

    def render(self, phase: float | None, *, tool_id: str = "", reviewing: bool = False) -> bool:
        """原位更新动作或核对结果的指示符，暂停与回放期间使用静态符号。"""
        if not self._actions or self.output.runtime.turn_output_suppressed:
            return False
        runtime = self.output.runtime
        if runtime.document.active_block is not None and runtime.document.active_source is not self._source:
            return False
        reviewing = reviewing and not any(action.pending for action in self._actions)
        owns_activity = reviewing or any(action.call_id == tool_id for action in self._actions)
        renderer = self._reviewing_renderer if reviewing else self._renderer
        if renderer is None:
            return False
        block = renderer(self.output.terminal_width)
        if owns_activity and phase is not None and self.output.animate and self.output.terminal_width >= 4 and (reviewing or any(action.pending for action in self._actions)):
            fragments = list(block.fragments)
            style, symbol = spinner_indicator_fragment(phase, family="wait", color_level=runtime.activity.color_level)
            fragments[0] = (style, symbol + " ")
            block = FragmentBlock(tuple(fragments))
        if block == self._last_frame:
            return owns_activity
        runtime.set_active_renderable(
            block, kind="operation", transcript_block=self._transcript,
            source=self._source, raw_text=self._raw,
            display_renderer=renderer, display_render_width=self.output.terminal_width,
        )
        self._last_frame = block
        return owns_activity

    def finish(self, status: str = "") -> None:
        """封闭整个会话，即使尚无分组或正在等待正文交接也不再接收展示。"""
        self._terminal = True
        with self.output.runtime.screen.visual_update():
            self._commit_group(status)
            groups = {group.target.stable_id: group for group in self._pending_groups.values()}
            for group in groups.values():
                self._replace_group(group, status)
            self._pending_groups.clear()
        self.output.runtime.activity.unbind_tool_renderer(self)

    def suspend(self) -> None:
        """提交当前相邻分组，保留会话对后续桌面调用和终态的观察。"""
        self._commit_group()

    def _commit_group(self, status: str = "") -> None:
        """在明确展示边界冻结一组；缺失结果单独保留，不伪造成功。"""
        if not self._actions:
            return
        runtime = self.output.runtime
        actions = _finished_actions(self._actions, status)
        renderer = partial(render_computer_fragment, actions, True, runtime.keymap.open_transcript_label)
        with runtime.screen.visual_update():
            if runtime.document.active_source is self._source:
                context = self.output.surface_context
                call_id = next((action.call_id for action in self._actions if action.call_id), "")
                target = runtime.commit_active_renderable(
                    renderer(self.output.terminal_width), transcript_block=self._transcript,
                    source=self._source, raw_text=self._raw,
                    display_renderer=renderer, display_render_width=self.output.terminal_width,
                    stable_id=f"computer:{context.surface_id if context is not None else ''}:{call_id}" if call_id else None,
                    transcript_stable=self._terminal or not any(action.pending and action.call_id for action in self._actions),
                )
                if not self._terminal:
                    group = _CommittedComputerGroup(target, self._actions, list(self._transcript_parts), list(self._raw_parts))
                    for action in self._actions:
                        if action.pending and action.call_id:
                            self._pending_groups[action.call_id] = group
            self._actions = ()
            self._transcript_parts.clear()
            self._raw_parts.clear()
            self._source = None
            self._raw = ""
            self._transcript = FragmentBlock(())
            self._renderer = None
            self._reviewing_renderer = None
            self._last_frame = None


if __name__ == '__main__':
    pass
