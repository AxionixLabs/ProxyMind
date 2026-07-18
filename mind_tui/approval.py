# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from prompt_toolkit.application import (
    Application,
    get_app
)
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Window
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
                height=self.content_height,
                dont_extend_height=True,
                style="class:approval.card",
                always_hide_cursor=True
            ),
            filter=Condition(lambda: self.visible)
        )

    def bind(self, application: Application[typing.Any]) -> None:
        """绑定拥有该浮层的应用。"""
        self._application = application

    def content_height(self) -> int:
        """返回审批卡当前内容需要的精确行数。"""
        return len(self._content_lines())

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
        """生成无边框的审批卡格式化文本。"""
        lines = self._content_lines()
        fragments: StyleAndTextTuples = []
        for index, (style, line) in enumerate(lines):
            fragments.append((style, _padded_line(line, self.WIDTH)))
            if index < len(lines) - 1:
                fragments.append(("", "\n"))
        return fragments

    def _content_lines(self) -> list[tuple[str, str]]:
        """生成审批卡的内容行和局部样式。"""
        approval_command = str(
            self.approval.get("command")
            or _approval_argument_command(self.approval)
        )
        if approval_command:
            prompt = "Would you like to run the following command?"
            command = approval_command
        else:
            prompt = str(
                self.approval.get("prompt")
                or "Would you like to run this tool?"
            )
            command = str(self.approval.get("tool") or "tool call")
        lines: list[tuple[str, str]] = [
            ("class:approval.title", f"  {prompt}"),
            ("class:approval.card", "")
        ]
        command_lines = _wrap_command(command, max_width=self.WIDTH - 4)
        for index, line in enumerate(command_lines):
            prefix = "  $ " if index == 0 else "    "
            lines.append(("class:approval.command", prefix + line))
        lines.append(("class:approval.card", ""))

        for index, decision in enumerate(self.decisions):
            marker = "›" if index == self.selected_index else " "
            label = _DECISION_LABELS[decision]
            style = "class:approval.selected" if index == self.selected_index else "class:approval.option"
            lines.append((style, f"  {marker} {index + 1}. {label}"))
        return lines


def _normalize_decisions(approval: dict[str, typing.Any]) -> list[ApprovalDecision]:
    """提取审批卡支持的选择并保持服务端顺序。"""
    raw = approval.get("availableDecisions", approval.get("available_decisions"))
    allowed = {"accept", "acceptForSession", "decline"}
    decisions = [item for item in raw or [] if item in allowed]
    return decisions or ["accept", "decline"]


def _approval_argument_command(approval: dict[str, typing.Any]) -> str:
    """从审批参数中提取单条命令文本。"""
    arguments = approval.get("arguments")
    if not isinstance(arguments, dict):
        return ""
    return str(arguments.get("command") or "").strip()


def _wrap_command(text: str, *, max_width: int) -> list[str]:
    """按终端显示宽度拆分命令文本。"""
    limit = max(1, int(max_width))
    lines: list[str] = []
    current = ""
    for character in str(text or ""):
        if character == "\n":
            lines.append(current)
            current = ""
            continue
        if current and get_cwidth(current + character) > limit:
            lines.append(current)
            current = character
        else:
            current += character
    lines.append(current)
    return lines or [""]


def _padded_line(text: str, width: int) -> str:
    """将审批卡单行补齐到指定显示宽度。"""
    value = str(text or "")
    padding = max(0, int(width) - get_cwidth(value))
    return value + " " * padding


if __name__ == '__main__':
    pass
