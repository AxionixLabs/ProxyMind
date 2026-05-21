# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
import asyncio
from dataclasses import (
    dataclass, field
)
from rich.console import Group
from rich.text import Text
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from mind_core.design import Design
from mind_app.stream_events.approval_trace import approval_summary

InputFunc = typing.Callable[[str], str]
ApprovalDecisionValue = typing.Literal[
    "accept",
    "acceptForSession",
    "decline",
    "cancel"
]
DEFAULT_APPROVAL_DECISIONS: tuple[ApprovalDecisionValue, ...] = (
    "accept",
    "decline"
)
DECISION_LABELS: dict[str, str] = {
    "accept"           : "Yes, proceed",
    "acceptForSession" : "Yes, for this session",
    "decline"          : "No",
    "cancel"           : "Cancel"
}
APPROVAL_MENU_STYLE = Style.from_dict({
    "radio-list"     : "",
    "radio"          : "#7F8C9A",
    "radio-selected" : "bold #A7C7FF",
    "radio-checked"  : "bold #E2E8F0",
    "radio-number"   : "#7F8C9A"
})


@dataclass(slots=True)
class ApprovalRecord(object):
    """保存审批请求元数据，用于校验后续工具调用。"""
    approval_id: str
    call_id: str
    tool: str
    command: list[typing.Any]
    cwd: str


@dataclass(slots=True)
class ApprovalDecision(object):
    """表示工具调用审批校验后的处理动作。"""
    action: typing.Literal["allow", "reject"]
    result: dict[str, typing.Any] | None = None


@dataclass(slots=True)
class ApprovalStore(object):
    """保存当前流式轮次内的审批状态。"""
    by_call_id: dict[str, ApprovalRecord] = field(default_factory=dict)
    approved_by_call_id: dict[str, str] = field(default_factory=dict)

    def remember_request(
        self,
        *,
        call_id: str,
        approval: dict[str, typing.Any]
    ) -> None:
        """按工具调用 ID 记录审批元数据。"""
        approval_id = str(approval.get("id") or "").strip()
        if not approval_id:
            return None
        command = approval.get("command")
        if not isinstance(command, list):
            command = []
        self.by_call_id[call_id] = ApprovalRecord(
            approval_id=approval_id,
            call_id=call_id,
            tool=str(approval.get("tool") or "shell_exec"),
            command=list(command),
            cwd=str(approval.get("cwd") or ".")
        )

    def mark_decision(
        self,
        *,
        call_id: str,
        approval: dict[str, typing.Any],
        decision: str
    ) -> None:
        """记录审批请求对应的用户选择。"""
        self.remember_request(call_id=call_id, approval=approval)
        approval_id = str(approval.get("id") or "").strip()
        if decision in {"accept", "acceptForSession"} and approval_id:
            self.approved_by_call_id[call_id] = approval_id
            return None
        self.approved_by_call_id.pop(call_id, None)


def approval_id_from_event(
    event: dict[str, typing.Any]
) -> str:
    """从流式事件中读取审批 ID。"""
    approval = event.get("approval") if isinstance(event.get("approval"), dict) else {}
    return str(approval.get("id") or "").strip()


def _same_command(
    left: typing.Any,
    right: typing.Any
) -> bool:
    """将命令数组元素转为字符串后进行比较。"""
    if not isinstance(left, list) or not isinstance(right, list):
        return False
    return [str(item) for item in left] == [str(item) for item in right]


def validate_shell_approval(
    *,
    event: dict[str, typing.Any],
    name: str,
    arguments: dict[str, typing.Any],
    store: ApprovalStore
) -> ApprovalDecision:
    """校验服务端已批准 shell 工具调用的审批元数据。"""
    if name != "shell_exec":
        return ApprovalDecision(action="allow")

    call_id = str(event.get("call_id") or "")
    approval_id = str(event.get("approval_id") or "").strip()
    event_approved = bool(event.get("approved"))

    if not event_approved and not approval_id:
        return ApprovalDecision(action="allow")

    record = store.by_call_id.get(call_id)

    if (
        event_approved
        and approval_id
        and record is not None
        and store.approved_by_call_id.get(call_id) == approval_id
    ):
        command = arguments.get("command")
        cwd = str(arguments.get("cwd") or ".")
        if approval_id != record.approval_id:
            return ApprovalDecision(
                action="reject",
                result=_approval_reject_result("approval_id mismatch")
            )
        if not _same_command(command, record.command) or cwd != record.cwd:
            return ApprovalDecision(
                action="reject",
                result=_approval_reject_result("approved command/cwd mismatch")
            )
        return ApprovalDecision(action="allow")

    return ApprovalDecision(
        action="reject",
        result=_approval_reject_result("approved shell call missing matching approval")
    )


def _approval_reject_result(
    message: str
) -> dict[str, typing.Any]:
    """构造审批校验未通过时的工具结果。"""
    return {
        "approval_denied": True, "error": message
    }


def approval_prompt_text(
    approval: dict[str, typing.Any]
) -> str:
    """构造审批提示的纯文本内容。"""
    summary = approval_summary(approval)
    return (
        "\nWould you like to run the following command?\n\n"
        f"$ {summary}\n"
    )


def approval_prompt_renderable(
    approval: dict[str, typing.Any]
) -> Group:
    """构造审批提示的终端渲染内容。"""
    summary = approval_summary(approval)
    return Group(
        Text("Would you like to run the following command?\n", style="bold #E2E8F0"),
        Text(f"$ {summary}", style="bold #D7E7FF"),
    )


def approval_decisions(
    approval: dict[str, typing.Any]
) -> list[ApprovalDecisionValue]:
    """读取服务端可用审批选项，并补齐安全默认值。"""
    raw = approval.get("availableDecisions", approval.get("available_decisions"))
    values = raw if isinstance(raw, list) else list(DEFAULT_APPROVAL_DECISIONS)
    out: list[ApprovalDecisionValue] = []
    for value in values:
        normalized = _normalize_decision(value)
        if normalized is not None and normalized not in out:
            out.append(normalized)
    if "decline" not in out:
        out.append("decline")
    return out or list(DEFAULT_APPROVAL_DECISIONS)


def approval_choice_parts(
    approval: dict[str, typing.Any]
) -> list[dict[str, str | None]]:
    """构造审批选项文本片段。"""
    parts: list[dict[str, str | None]] = []
    for index, decision in enumerate(approval_decisions(approval), start=1):
        prefix = "›" if index == 1 else " "
        if parts:
            parts.append({"text": "\n", "style": None})
        parts.extend([
            {"text": f"{prefix} {index}. ", "style": "bold #A7C7FF" if index == 1 else "#7F8C9A"},
            {"text": DECISION_LABELS.get(decision, decision), "style": "bold #E2E8F0" if index == 1 else "#9AA9B5"},
        ])
    parts.append({"text": "\n", "style": None})
    return parts


def approval_choice_text(
    approval: dict[str, typing.Any]
) -> str:
    """构造审批选项纯文本。"""
    lines = []
    for index, decision in enumerate(approval_decisions(approval), start=1):
        prefix = "›" if index == 1 else " "
        lines.append(f"{prefix} {index}. {DECISION_LABELS.get(decision, decision)}")
    return "\n".join(lines) + "\n"


def approval_prompt_parts(
    approval: dict[str, typing.Any]
) -> list[dict[str, str | None]]:
    """构造审批提示的文本片段。"""
    summary = approval_summary(approval)
    return [
        {"text": "Would you like to run the following command?\n\n", "style": "bold #E2E8F0"},
        {"text": f"$ {summary}\n", "style": "bold #D7E7FF"}
    ]


def _approval_choice_renderable(approval: dict[str, typing.Any]) -> Text:
    out = Text()
    for part in approval_choice_parts(approval):
        out.append(str(part.get("text") or ""), style=part.get("style"))
    return out


def _normalize_decision(value: typing.Any) -> ApprovalDecisionValue | None:
    text = str(value or "").strip()
    aliases: dict[str, ApprovalDecisionValue] = {
        "accept"             : "accept",
        "approve"            : "accept",
        "approved"           : "accept",
        "yes"                : "accept",
        "accept_for_session" : "acceptForSession",
        "accept-for-session" : "acceptForSession",
        "acceptforsession"   : "acceptForSession",
        "decline"            : "decline",
        "deny"               : "decline",
        "denied"             : "decline",
        "no"                 : "decline",
        "cancel"             : "cancel"
    }
    return aliases.get(text.lower())


def _answer_to_decision(
    answer: typing.Any,
    decisions: list[ApprovalDecisionValue]
) -> ApprovalDecisionValue:
    text = str(answer or "").strip().lower()
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


def _set_terminal_cursor_visible(
    visible: bool
) -> None:
    sys.stdout.write("\x1b[?25h" if visible else "\x1b[?25l")
    sys.stdout.flush()


async def prompt_tool_approval_decision(
    approval: dict[str, typing.Any],
    *,
    input_func: InputFunc | None = None,
    show_prompt: bool = True
) -> ApprovalDecisionValue:
    """读取审批请求的用户选择。"""
    decisions = approval_decisions(approval)
    try:
        if input_func is not None:
            answer = await asyncio.to_thread(
                input_func,
                approval_prompt_text(approval) + "\n" + approval_choice_text(approval)
            )
        else:
            if show_prompt:
                Design.console.print()
                Design.console.print(approval_prompt_renderable(approval))
                Design.console.print(_approval_choice_renderable(approval))
            answer = await _run_approval_menu(decisions)
    except (EOFError, KeyboardInterrupt):
        return "decline"
    return _answer_to_decision(answer, decisions)


async def _run_approval_menu(
    decisions: list[ApprovalDecisionValue]
) -> str:
    bindings = KeyBindings()
    selected = [0]

    def render_menu() -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []
        for _index, _decision in enumerate(decisions, start=1):
            active = _index - 1 == selected[0]
            style = "class:radio-selected" if active else "class:radio"
            prefix = "›" if active else " "
            label = DECISION_LABELS.get(_decision, _decision)
            parts.append((style, f"{prefix} {_index}. {label}"))
            if _index < len(decisions):
                parts.append(("", "\n"))
        return parts

    @bindings.add("enter")
    def _(event) -> None:
        event.app.exit(result=decisions[selected[0]])

    @bindings.add("down")
    @bindings.add("c-n")
    def _(event) -> None:
        selected[0] = (selected[0] + 1) % len(decisions)
        event.app.invalidate()

    @bindings.add("up")
    @bindings.add("c-p")
    def _(event) -> None:
        selected[0] = (selected[0] - 1) % len(decisions)
        event.app.invalidate()

    @bindings.add("escape")
    @bindings.add("c-c")
    def _(event) -> None:
        event.app.exit(result="decline")

    @bindings.add("y")
    def _(event) -> None:
        if "accept" in decisions:
            event.app.exit(result="accept")

    @bindings.add("n")
    def _(event) -> None:
        event.app.exit(result="decline" if "decline" in decisions else decisions[-1])

    for option_index, option_decision in enumerate(decisions, start=1):
        @bindings.add(str(option_index))
        def _(event, selected_decision=option_decision) -> None:
            event.app.exit(result=selected_decision)

    control = FormattedTextControl(render_menu, focusable=True)
    app: Application[str] = Application(
        layout=Layout(
            Window(
                content=control,
                height=len(decisions),
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=APPROVAL_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
    )
    _set_terminal_cursor_visible(False)
    try:
        return str(await app.run_async())
    finally:
        _set_terminal_cursor_visible(True)


if __name__ == '__main__':
    pass
