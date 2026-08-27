# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from mind_app.approval.models import (
    ApprovalDecisionValue,
    ApprovalQueueSnapshot
)
from mind_core.design.terminal_capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities
)
from mind_app.approval.policy import approval_decisions
from ..contracts.pager import StaticPagerRequest
from .approval_render import (
    approval_command_pager_lines,
    approval_pager_title,
    tui_approval_content_lines,
)


@dataclass(slots=True)
class ApprovalState(object):
    """保存审批表面当前展示的单条请求。"""
    approval: dict[str, typing.Any]
    decisions: list[ApprovalDecisionValue]
    future: asyncio.Future[ApprovalDecisionValue]


class TuiApproval(object):
    """展示协调器指定的当前审批请求并上报用户动作。"""

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        focus_card: typing.Callable[[], None],
        focus_input: typing.Callable[[], None],
        get_width: typing.Callable[[], int],
        get_max_height: typing.Callable[[], int],
        terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES,
        open_static_pager: typing.Callable[[StaticPagerRequest], bool] | None = None,
    ) -> None:
        self.invalidate     = invalidate
        self.focus_card     = focus_card
        self.focus_input    = focus_input
        self.get_width      = get_width
        self.get_max_height = get_max_height

        self.terminal_capabilities = terminal_capabilities
        self.open_static_pager     = open_static_pager

        self.state: ApprovalState | None = None

        self._default_wait_state: ApprovalState | None = None

        self.selected_index: int = 0

        self._session_active: bool = False

        self._snapshot = ApprovalQueueSnapshot(
            current=None,
            pending=(),
            revision=0,
        )
        self.key_bindings = self._build_key_bindings()

    @property
    def active(self) -> bool:
        """返回审批批次是否正在占用交互表面。"""
        return self._session_active

    @property
    def pending_count(self) -> int:
        """返回应用层快照中的排队审批数量。"""
        return self._snapshot.pending_count

    def begin_session(self) -> None:
        """激活连续审批批次并接管 bottom pane 焦点。"""
        if self._session_active:
            return None
        self._session_active = True
        self.focus_card()
        self.invalidate()

    def snapshot_changed(self, snapshot: ApprovalQueueSnapshot) -> None:
        """保存应用层审批队列的最新只读快照。"""
        if (
            snapshot.coordinator_id == self._snapshot.coordinator_id
            and snapshot.revision < self._snapshot.revision
        ):
            return None
        self._snapshot = snapshot
        self.invalidate()

    async def request(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """展示一条审批并等待当前用户动作。"""
        owns_session = not self._session_active
        if owns_session:
            self.begin_session()
        state = self._present(approval)

        try:
            return await state.future
        except BaseException:
            if not state.future.done():
                state.future.cancel()
            raise
        finally:
            self._clear_state(state)
            if owns_session:
                await self.end_session()

    def begin(self, approval: dict[str, typing.Any]) -> bool:
        """建立单条审批状态，供控件级调用方分步等待。"""
        if not self._session_active:
            self.begin_session()
        state = self._present(approval)
        self._default_wait_state = state
        return state is not None

    async def wait(
        self,
        *,
        state: ApprovalState | None = None,
    ) -> ApprovalDecisionValue:
        """等待指定审批状态或分步建立的默认状态。"""
        current = state or self._default_wait_state or self.state
        if current is None:
            raise RuntimeError("cannot wait without an active TUI approval")
        result = await current.future
        if self._default_wait_state is current:
            self._default_wait_state = None
        return result

    async def dismiss(self) -> None:
        """拒绝当前请求并结束控件级审批批次。"""
        await self.end_session()

    async def end_session(self, *, restore_focus: bool = True) -> None:
        """结束审批批次并按需恢复原交互表面。"""
        state = self.state
        self.state = None
        self._default_wait_state = None
        self.selected_index = 0
        was_active = self._session_active
        self._session_active = False

        if state is not None and not state.future.done():
            state.future.set_result("decline")
        if was_active and restore_focus:
            self.focus_input()
        if state is not None or was_active:
            self.invalidate()

    async def close(self) -> None:
        """关闭审批表面并拒绝仍在展示的请求。"""
        await self.end_session(restore_focus=False)

    def fragments(self) -> StyleAndTextTuples:
        """生成带表面背景的审批卡片内容。"""
        card_lines, _footer_lines = self._render_lines()
        return self._format_lines(card_lines)

    def footer_fragments(self) -> StyleAndTextTuples:
        """生成审批卡片下方的透明操作提示。"""
        _card_lines, footer_lines = self._render_lines()
        return self._format_lines(
            footer_lines,
            prefix_style="class:approval-footer",
        )

    def _render_lines(
        self,
    ) -> tuple[
        list[list[tuple[str, str]]],
        list[list[tuple[str, str]]],
    ]:
        """生成审批布局并按表面和提示区域拆分。"""
        state = self.state
        if state is None:
            return [], []

        lines = tui_approval_content_lines(
            state.decisions,
            approval=state.approval,
            pending_count=self.pending_count,
            selected_index=self.selected_index,
            width=max(1, self.get_width() - 4),
            max_height=self.get_max_height(),
        )
        footer_start = next((
            index
            for index, line in enumerate(lines)
            if any(style == "class:approval-footer" for style, _text in line)
        ), len(lines))
        return lines[:footer_start], lines[footer_start:]

    @staticmethod
    def _format_lines(
        lines: list[list[tuple[str, str]]],
        *,
        prefix_style: str = "class:approval-card",
    ) -> StyleAndTextTuples:
        """把审批行转换为带统一左侧留白的格式化文本。"""
        if not lines:
            return []

        out: StyleAndTextTuples = []
        for index, line in enumerate(lines):
            out.append((prefix_style, "  "))
            out.extend(line)
            if index < len(lines) - 1:
                out.append((prefix_style, "\n"))
        return out

    def finish(self, decision: ApprovalDecisionValue) -> None:
        """在当前请求允许时提交审批结果。"""
        state = self.state
        if state is None or state.future.done():
            return None
        if decision not in state.decisions and decision != "cancel":
            return None
        state.future.set_result(decision)

    def _clear_state(self, state: ApprovalState) -> None:
        """只清理仍属于本次展示的请求状态。"""
        if self.state is not state:
            return None
        self.state = None
        self.selected_index = 0
        self.invalidate()

    def _present(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalState:
        """建立当前单条展示状态。"""
        if self.state is not None:
            raise RuntimeError("cannot present multiple TUI approvals")
        state = ApprovalState(
            approval=dict(approval),
            decisions=approval_decisions(approval),
            future=asyncio.get_running_loop().create_future(),
        )
        self.state = state
        self.selected_index = 0
        self.invalidate()
        return state

    def _move(self, step: int) -> None:
        """移动审批卡当前选择。"""
        state = self.state
        if state is None or not state.decisions:
            return None
        self.selected_index = (
            self.selected_index + step
        ) % len(state.decisions)
        self.invalidate()

    def _finish_index(self, index: int) -> None:
        """按审批选项索引完成当前请求。"""
        state = self.state
        if state is None or not (0 <= index < len(state.decisions)):
            return None
        self.finish(state.decisions[index])

    def _build_key_bindings(self) -> KeyBindings:
        """创建审批卡局部按键绑定。"""
        bindings = KeyBindings()

        @bindings.add("c-a")
        @bindings.add("A")
        def _(event) -> None:
            _ = event
            state = self.state
            if state is None or self.open_static_pager is None:
                return None
            self.open_static_pager(StaticPagerRequest(
                title=approval_pager_title(state.approval),
                lines=approval_command_pager_lines(
                    state.approval,
                    terminal_capabilities=self.terminal_capabilities,
                ),
            ))

        @bindings.add("enter")
        def _(event) -> None:
            if self.state is not None:
                self._finish_index(self.selected_index)

        @bindings.add("down")
        @bindings.add("c-n")
        def _(event) -> None:
            self._move(1)

        @bindings.add("up")
        @bindings.add("c-p")
        def _(event) -> None:
            self._move(-1)

        @bindings.add(Keys.Escape, eager=True)
        @bindings.add("n")
        def _(event) -> None:
            self.finish("decline")

        @bindings.add("y")
        def _(event) -> None:
            self.finish("accept")

        @bindings.add("s")
        def _(event) -> None:
            self.finish("acceptForSession")

        @bindings.add("p")
        def _(event) -> None:
            self.finish("acceptWithExecpolicyAmendment")

        @bindings.add("c-c")
        def _(event) -> None:
            self.finish("cancel")

        for number in range(1, 10):
            @bindings.add(str(number))
            def _(event, selected_number=number) -> None:
                self._finish_index(selected_number - 1)

        return bindings


if __name__ == '__main__':
    pass
