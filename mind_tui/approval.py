# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from prompt_toolkit.application import (
    Application, get_app
)
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer, Window
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.utils import get_cwidth

ApprovalDecision = typing.Literal["accept", "acceptForSession", "decline"]

_DECISION_LABELS: dict[ApprovalDecision, str] = {
    "accept": "Yes",
    "acceptForSession": "Yes, and don't ask again for this session",
    "decline": "No, and tell Mind what to do differently"
}


class ApprovalOverlay:
    """管理审批卡浮层、焦点和用户选择。"""

    WIDTH = 72

    def __init__(self) -> None:
        self.visible = False
        self.approval: dict[str, typing.Any] = {}
        self.decisions: list[ApprovalDecision] = ["accept", "decline"]
        self.selected_index = 0
        self._future: asyncio.Future[ApprovalDecision] | None = None
        self._previous_control: typing.Any = None
        self._application: Application[typing.Any] | None = None

        self.control = FormattedTextControl(
            self._render,
            focusable=True,
            modal=True,
            key_bindings=self._build_key_bindings()
        )
        self.container = ConditionalContainer(
            Window(
                self.control,
                width=Dimension(preferred=self.WIDTH),
                height=Dimension(preferred=13, max=16),
                always_hide_cursor=True
            ),
            filter=Condition(lambda: self.visible)
        )

    def bind(self, application: Application[typing.Any]) -> None:
        """绑定拥有该浮层的应用。"""
        self._application = application

    async def show(self, approval: dict[str, typing.Any]) -> ApprovalDecision:
        """显示审批卡并等待用户完成选择。"""
        if self.visible and self._future is not None:
            return await self._future

        self.approval = dict(approval)
        self.decisions = _normalize_decisions(approval)
        self.selected_index = 0
        self.visible = True
        self._future = asyncio.get_running_loop().create_future()

        app = self._application or get_app()
        self._previous_control = app.layout.current_control
        app.layout.focus(self.control)
        app.invalidate()

        return await self._future

    def _finish(self, decision: ApprovalDecision) -> None:
        """结束审批并恢复浮层打开前的焦点。"""
        if not self.visible:
            return

        self.visible = False
        future = self._future
        self._future = None

        app = self._application or get_app()
        if self._previous_control is not None:
            app.layout.focus(self._previous_control)
        app.invalidate()

        if future is not None and not future.done():
            future.set_result(decision)

    def _build_key_bindings(self) -> KeyBindings:
        """创建审批卡的局部按键绑定。"""
        bindings = KeyBindings()

        @bindings.add("up")
        @bindings.add("c-p")
        def _previous(event) -> None:
            self.selected_index = (self.selected_index - 1) % len(self.decisions)
            event.app.invalidate()

        @bindings.add("down")
        @bindings.add("c-n")
        def _next(event) -> None:
            self.selected_index = (self.selected_index + 1) % len(self.decisions)
            event.app.invalidate()

        @bindings.add("enter")
        def _accept_selected(event) -> None:
            self._finish(self.decisions[self.selected_index])

        @bindings.add("y")
        def _accept(event) -> None:
            if "accept" in self.decisions:
                self._finish("accept")

        @bindings.add("s")
        def _accept_session(event) -> None:
            if "acceptForSession" in self.decisions:
                self._finish("acceptForSession")

        @bindings.add("n")
        @bindings.add("escape", eager=True)
        @bindings.add("c-c")
        def _decline(event) -> None:
            if "decline" in self.decisions:
                self._finish("decline")

        return bindings

    def _render(self) -> StyleAndTextTuples:
        """生成审批卡的无背景色格式化文本。"""
        width = self.WIDTH
        title = str(self.approval.get("title") or "Review command")
        prompt = str(self.approval.get("prompt") or "Allow Mind to run this tool?")
        command = str(
            self.approval.get("command")
            or self.approval.get("tool")
            or "tool call"
        )

        lines: list[tuple[str, str]] = [
            ("class:approval.border", _top_border(title, width)),
            ("class:approval.border", _card_line("", width)),
            ("class:approval.title", _card_line(prompt, width)),
            ("class:approval.command", _card_line(f"$ {command}", width)),
            ("class:approval.border", _card_line("", width))
        ]

        for index, decision in enumerate(self.decisions):
            marker = "›" if index == self.selected_index else " "
            label = _DECISION_LABELS[decision]
            style = "class:approval.selected" if index == self.selected_index else "class:approval.option"
            lines.append((style, _card_line(f"{marker} {index + 1}. {label}", width)))

        lines.extend([
            ("class:approval.border", _card_line("", width)),
            ("class:approval.border", "└" + "─" * (width - 2) + "┘")
        ])

        fragments: StyleAndTextTuples = []
        for index, (style, line) in enumerate(lines):
            fragments.append((style, line))
            if index < len(lines) - 1:
                fragments.append(("", "\n"))
        return fragments


def _normalize_decisions(approval: dict[str, typing.Any]) -> list[ApprovalDecision]:
    """提取审批卡支持的选择并保持服务端顺序。"""
    raw = approval.get("availableDecisions", approval.get("available_decisions"))
    allowed = {"accept", "acceptForSession", "decline"}
    decisions = [item for item in raw or [] if item in allowed]
    return decisions or ["accept", "decline"]


def _top_border(title: str, width: int) -> str:
    """生成带标题的审批卡上边框。"""
    label = f" {title.strip()} "
    available = max(0, width - 4)
    if get_cwidth(label) > available:
        label = label[: max(0, available - 1)] + "…"
    rule = max(0, width - 2 - get_cwidth(label))
    return "┌" + label + "─" * rule + "┐"


def _card_line(text: str, width: int) -> str:
    """生成固定显示宽度的审批卡正文行。"""
    available = max(1, width - 4)
    original = str(text or "")
    value = original
    while value and get_cwidth(value) > available - 1:
        value = value[:-1]
    if get_cwidth(original) > available:
        value += "…"
    padding = max(0, available - get_cwidth(value))
    return f"│ {value}{' ' * padding} │"


if __name__ == '__main__':
    pass
