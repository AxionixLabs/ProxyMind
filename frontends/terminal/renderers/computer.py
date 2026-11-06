# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.application.views.tools import (
    NativeToolResultView,
    ToolStartView,
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)
from frontends.terminal.text import sanitize_terminal_line
from frontends.terminal.text_layout import clip_display_text


_ACTION_LABELS = {
    "list_windows": ("Finding windows", "Listed windows"),
    "observe": ("Inspecting window", "Inspected window"),
    "validate_observation": ("Checking observation", "Checked observation"),
    "activate": ("Activating window", "Activated window"),
    "click": ("Clicking", "Clicked"),
    "move": ("Moving pointer", "Moved pointer"),
    "drag": ("Dragging", "Dragged"),
    "scroll": ("Scrolling", "Scrolled"),
    "press_key": ("Pressing keys", "Pressed keys"),
    "type_text": ("Typing text", "Typed text"),
    "reset": ("Resetting desktop session", "Reset desktop session"),
}


@dataclass(frozen=True, slots=True)
class ComputerAction:
    """保存展示所需的单次桌面调用事实，不推断应用中的操作是否达成目标。"""

    call_id: str
    action: str
    screenshot: bool = False
    ok: bool | None = None
    error: str = ""
    unresolved: str = ""

    @property
    def pending(self) -> bool:
        """返回尚未收到结果且未结束观察的调用。"""
        return self.ok is None and not self.unresolved


def computer_action(view: ToolStartView | NativeToolResultView) -> ComputerAction:
    """从既有工具视图收窄动作与错误码，默认摘要不展示输入文本或原始载荷。"""
    action = view.arguments.get("action")
    error = ""
    if isinstance(view, NativeToolResultView) and not view.ok:
        data = view.data
        if isinstance(data, dict):
            value = data.get("error")
            if isinstance(value, str):
                error = value
    return ComputerAction(
        call_id=view.call_id,
        action=action if isinstance(action, str) else "",
        screenshot=(isinstance(view.data, dict) and isinstance(view.data.get("image"), dict))
        if isinstance(view, NativeToolResultView) else view.arguments.get("screenshot") is True,
        ok=view.ok if isinstance(view, NativeToolResultView) else None,
        error=error,
    )


def render_computer_activity(
    actions: tuple[ComputerAction, ...],
    *,
    finished: bool,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
    key_label: str = "",
    reviewing: bool = False,
) -> StyledBlock:
    """生成固定两行的桌面操作摘要，完整记录由调用方独立保留。"""
    count = len(actions)
    failures = sum(action.ok is False for action in actions)
    unresolved = sum(bool(action.unresolved) for action in actions)
    label = "Used computer" if finished else "Using computer"
    suffix = f" · {count} {'action' if count == 1 else 'actions'}"
    if failures:
        suffix += f" · {failures} failed"
    if unresolved:
        status = "interrupted" if any(action.unresolved == "interrupted" for action in actions) else "unconfirmed"
        suffix += f" · {unresolved} {status}"
    selected = next((action for action in reversed(actions) if action.pending), None)
    if selected is None:
        selected = next((action for action in reversed(actions) if action.ok is False or action.unresolved), actions[-1])
    labels = _ACTION_LABELS.get(selected.action, ("Computer action", "Computer action finished"))
    detail = labels[0] if selected.pending else labels[1]
    if selected.screenshot and selected.action == "observe" and selected.ok:
        detail = "Captured screenshot"
    if selected.ok is False:
        detail = f"Failed: {labels[0]}" + (f" · {selected.error}" if selected.error else "")
    elif selected.unresolved:
        detail = f"{labels[0]} · result unconfirmed"
    elif reviewing and not selected.pending:
        detail += " · reviewing result"
    if finished and count > 1 and key_label:
        detail += f" · {key_label} details"
    width = max(1, terminal_width or 120)
    if width < 4:
        return StyledBlock(plain_text=clip_display_text("• " + label, width=width, measure_width=measure_width))
    title = clip_display_text(label + suffix, width=max(0, width - 2), measure_width=measure_width)
    detail = clip_display_text(sanitize_terminal_line(detail), width=max(0, width - 4), measure_width=measure_width)
    secondary = TextStyle(dim=True)
    detail_style = semantic_text_style(TerminalSemanticRole.FAILURE) if selected.ok is False else secondary
    spans = (
        TextSpan("• ", secondary),
        TextSpan(title[:len(label)], TextStyle(bold=True)),
        TextSpan(title[len(label):], secondary),
        TextSpan("\n  └ ", secondary),
        TextSpan(detail, detail_style),
    )
    return StyledBlock(plain_text="".join(span.text for span in spans), spans=spans)


if __name__ == '__main__':
    pass
