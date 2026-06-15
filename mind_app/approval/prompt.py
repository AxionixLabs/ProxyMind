# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import shutil
import typing
import asyncio
import contextlib
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from .models import ApprovalDecisionValue
from .policy import (
    approval_choice_text,
    approval_decisions,
    approval_expired,
    approval_prompt_text,
    approval_remaining_sec,
    _normalize_decision
)
from .render import (
    APPROVAL_MENU_STYLE,
    approval_menu_content_lines,
    approval_menu_plain_text,
    approval_title,
    render_bordered_approval_menu
)

def _terminal_menu_width() -> int:
    """返回审批菜单可用的终端宽度。"""
    return max(40, shutil.get_terminal_size(fallback=(100, 24)).columns)


def _terminal_menu_height() -> int:
    """返回审批菜单可用的终端高度。"""
    return max(12, shutil.get_terminal_size(fallback=(100, 24)).lines)


def _answer_to_decision(
    answer: typing.Any,
    decisions: list[ApprovalDecisionValue]
) -> ApprovalDecisionValue:
    """将用户输入转换为最终审批选择。"""
    text = str(answer or "").strip().lower()
    if text == "expired":
        return "expired"
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(decisions):
            return decisions[index]
    if text in {"y", "yes"} and "accept" in decisions:
        return "accept"
    if text in {"n", "no", ""}:
        return "decline" if "decline" in decisions else decisions[-1]

    normalized = _normalize_decision(text)
    if normalized is not None and normalized in decisions:
        return normalized

    return "decline" if "decline" in decisions else decisions[-1]


async def prompt_tool_approval_decision(
    approval: dict[str, typing.Any],
    *,
    input_func: typing.Callable[[str], str] = None,
    show_prompt: bool = True
) -> ApprovalDecisionValue:
    """读取审批请求的用户选择。"""
    if approval_expired(approval):
        return "expired"

    decisions = approval_decisions(approval)
    try:
        if input_func is not None:
            answer = await asyncio.to_thread(
                input_func,
                approval_prompt_text(approval) + "\n" + approval_choice_text(approval)
            )
        else:
            answer = await _run_approval_menu(
                decisions, approval=approval if show_prompt else None
            )
    except (EOFError, KeyboardInterrupt):
        return "decline"

    return _answer_to_decision(answer, decisions)


async def _run_approval_menu(
    decisions: list[ApprovalDecisionValue],
    *,
    approval: dict[str, typing.Any] | None = None
) -> str:
    """运行交互式审批菜单并返回选择结果。"""
    bindings = KeyBindings()
    selected = [0]

    def content_lines() -> list[list[tuple[str, str]]]:
        """生成审批菜单未加边框的内容行。"""
        return approval_menu_content_lines(
            decisions,
            approval=approval,
            selected_index=selected[0]
        )

    def render_menu() -> list[tuple[str, str]]:
        """生成当前审批菜单的格式化文本片段。"""
        return render_bordered_approval_menu(
            content_lines(),
            max_width=_terminal_menu_width(),
            max_height=_terminal_menu_height(),
            title=approval_title(approval) if approval is not None else None
        )

    def menu_height() -> int:
        """返回 prompt_toolkit 窗口需要显示的行数。"""
        text = approval_menu_plain_text(render_menu())
        return text.count("\n") + 1

    @bindings.add("enter")
    def _(event) -> None:
        """确认当前选中的审批选项。"""
        event.app.exit(result=decisions[selected[0]])

    @bindings.add("down")
    @bindings.add("c-n")
    def _(event) -> None:
        """将菜单选择移动到下一项。"""
        selected[0] = (selected[0] + 1) % len(decisions)
        event.app.invalidate()

    @bindings.add("up")
    @bindings.add("c-p")
    def _(event) -> None:
        """将菜单选择移动到上一项。"""
        selected[0] = (selected[0] - 1) % len(decisions)
        event.app.invalidate()

    @bindings.add("escape")
    @bindings.add("c-c")
    def _(event) -> None:
        """取消审批菜单。"""
        if "cancel" in decisions:
            event.app.exit(result="cancel")
        elif "decline" in decisions:
            event.app.exit(result="decline")
        else:
            event.app.exit(result=decisions[-1])

    @bindings.add("y")
    def _(event) -> None:
        """通过快捷键接受审批请求。"""
        if "accept" in decisions:
            event.app.exit(result="accept")

    @bindings.add("s")
    def _(event) -> None:
        """通过快捷键在当前会话接受审批请求。"""
        if "acceptForSession" in decisions:
            event.app.exit(result="acceptForSession")

    @bindings.add("n")
    def _(event) -> None:
        """通过快捷键拒绝审批请求。"""
        if "decline" in decisions:
            event.app.exit(result="decline")
        elif "cancel" in decisions:
            event.app.exit(result="cancel")
        else:
            event.app.exit(result=decisions[-1])

    for option_index, option_decision in enumerate(decisions, start=1):
        @bindings.add(str(option_index))
        def _(event, selected_decision=option_decision) -> None:
            """通过数字快捷键选择审批选项。"""
            event.app.exit(result=selected_decision)

    control = FormattedTextControl(render_menu, focusable=True)

    app: Application[str] = Application(
        layout=Layout(
            Window(
                content=control,
                height=menu_height(),
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=APPROVAL_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    expiry_task = asyncio.create_task(_expire_approval_menu(app, approval))

    try:
        return str(await app.run_async())
    finally:
        expiry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await expiry_task


async def _expire_approval_menu(
    app: Application[str],
    approval: dict[str, typing.Any] | None
) -> None:
    """刷新审批倒计时，并在过期后自动关闭菜单。"""
    while True:
        remaining = approval_remaining_sec(approval)
        if remaining is None:
            return None
        if remaining <= 0:
            with contextlib.suppress(Exception):
                app.exit(result="expired")
            return None

        await asyncio.sleep(min(1.0, max(0.05, remaining)))

        with contextlib.suppress(Exception):
            app.invalidate()




if __name__ == '__main__':
    pass
