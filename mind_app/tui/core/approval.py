# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from dataclasses import dataclass
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.approval.policy import (
    approval_decisions,
    approval_expired,
    approval_remaining_sec,
)
from mind_app.approval.render import approval_menu_content_lines


@dataclass(slots=True)
class ApprovalState(object):
    """保存审批层当前请求、选择位置和等待结果。"""

    approval: dict[str, typing.Any]
    decisions: list[ApprovalDecisionValue]
    future: asyncio.Future[ApprovalDecisionValue]
    selected: int = 0


class TuiApproval(object):
    """管理持久 TUI 内的无边框审批交互状态。"""

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        focus_card: typing.Callable[[], None],
        focus_input: typing.Callable[[], None],
    ) -> None:
        self.invalidate = invalidate
        self.focus_card = focus_card
        self.focus_input = focus_input
        self.state: ApprovalState | None = None
        self.expiry_task: asyncio.Task[None] | None = None
        self.key_bindings = self._build_key_bindings()

    @property
    def active(self) -> bool:
        """返回当前是否存在审批请求。"""
        return self.state is not None

    async def request(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """显示审批内容并等待当前请求结果。"""
        if approval_expired(approval):
            return "expired"

        future = asyncio.get_running_loop().create_future()
        self.state = ApprovalState(
            approval=dict(approval),
            decisions=approval_decisions(approval),
            future=future,
        )
        self.focus_card()
        self.invalidate()
        self.expiry_task = asyncio.create_task(
            self._expire(),
            name="mind tui approval expiry",
        )

        try:
            return await future
        finally:
            await self._cancel_expiry()
            self.state = None
            self.focus_input()
            self.invalidate()

    async def close(self) -> None:
        """安全结束当前审批请求并清理倒计时。"""
        state = self.state
        if state is not None and not state.future.done():
            state.future.set_result("decline")
        self.state = None
        await self._cancel_expiry()

    def fragments(self) -> StyleAndTextTuples:
        """生成深灰背景无边框审批卡内容。"""
        state = self.state
        if state is None:
            return []

        lines = approval_menu_content_lines(
            state.decisions,
            approval=state.approval,
            selected_index=state.selected,
        )
        out: StyleAndTextTuples = [("class:approval-card", "\n")]
        for line in lines:
            out.append(("class:approval-card", "  "))
            out.extend(line or [("class:approval-card", " ")])
            out.append(("class:approval-card", "\n"))
        out.append(("class:approval-card", "\n"))
        return out

    def finish(self, decision: ApprovalDecisionValue) -> None:
        """在当前请求允许时提交审批结果。"""
        state = self.state
        if state is None or state.future.done():
            return None
        if decision not in state.decisions and decision != "expired":
            return None
        state.future.set_result(decision)

    def _move(self, step: int) -> None:
        """移动审批卡当前选择。"""
        state = self.state
        if state is None or not state.decisions:
            return None
        state.selected = (state.selected + step) % len(state.decisions)
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

        @bindings.add("enter")
        def _(event) -> None:
            state = self.state
            if state is not None:
                self._finish_index(state.selected)

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

        @bindings.add("c-c")
        def _(event) -> None:
            self.finish("decline")

        for number in range(1, 10):
            @bindings.add(str(number))
            def _(event, selected_number=number) -> None:
                self._finish_index(selected_number - 1)

        return bindings

    async def _expire(self) -> None:
        """刷新审批倒计时并在过期时结束请求。"""
        while self.state is not None:
            remaining = approval_remaining_sec(self.state.approval)
            if remaining is None:
                return None
            if remaining <= 0:
                self.finish("expired")
                return None
            await asyncio.sleep(min(1.0, max(0.05, remaining)))
            self.invalidate()

    async def _cancel_expiry(self) -> None:
        """取消当前审批倒计时任务。"""
        task = self.expiry_task
        self.expiry_task = None
        if task is None:
            return None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


if __name__ == '__main__':
    pass
