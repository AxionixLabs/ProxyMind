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
from mind_app.stream_events.approval_trace import (
    APPROVAL_ARG_STYLE,
    APPROVAL_COMMAND_STYLE,
    APPROVAL_PENDING_STYLE,
    APPROVAL_PROMPT_STYLE,
    APPROVAL_TOOL_STYLE,
    approval_summary
)

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
    "radio"          : "bold #9AA9B5",
    "radio-selected" : "bold #A7C7FF",
    "radio-checked"  : "bold #E2E8F0",
    "radio-number"   : "bold #9AA9B5"
})

@dataclass(slots=True)
class ApprovalRecord(object):
    """保存审批请求元数据，用于校验后续工具调用。"""
    approval_id: str
    call_id: str
    tool: str
    arguments: dict[str, typing.Any]


@dataclass(slots=True)
class ApprovalDecision(object):
    """表示工具调用审批校验后的处理动作。"""
    action: typing.Literal["allow", "reject", "wait"]
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
        tool = str(approval.get("tool") or "shell_exec").strip() or "shell_exec"
        self.by_call_id[call_id] = ApprovalRecord(
            approval_id=approval_id,
            call_id=call_id,
            tool=tool,
            arguments=_approval_arguments(approval)
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


def validate_tool_approval(
    *,
    event: dict[str, typing.Any],
    name: str,
    arguments: dict[str, typing.Any],
    store: ApprovalStore,
    meta: dict[str, typing.Any] | None = None,
    tool_meta: dict[str, typing.Any] | None = None
) -> ApprovalDecision:
    """校验服务端已批准工具调用的审批元数据。"""
    tool_name = str(name or "").strip()
    if not tool_name:
        return ApprovalDecision(action="allow")

    call_id        = str(event.get("call_id") or "")
    approval_id    = str(event.get("approval_id") or "").strip()
    event_approved = bool(event.get("approved"))
    effective_meta = (
        {**tool_meta, **meta}
        if isinstance(tool_meta, dict) and isinstance(meta, dict)
        else meta if isinstance(meta, dict)
        else tool_meta if isinstance(tool_meta, dict)
        else None
    )

    if not event_approved and not approval_id:
        if approval_required(event=event, meta=effective_meta):
            return ApprovalDecision(action="wait")
        return ApprovalDecision(action="allow")

    record = store.by_call_id.get(call_id)

    if (
        event_approved
        and approval_id
        and record is not None
        and store.approved_by_call_id.get(call_id) == approval_id
    ):
        if approval_id != record.approval_id:
            return ApprovalDecision(
                action="reject",
                result=_approval_reject_result("approval_id mismatch")
            )
        if tool_name != record.tool:
            return ApprovalDecision(
                action="reject",
                result=_approval_reject_result("approved tool mismatch")
            )
        if _canonical_tool_arguments(arguments) != record.arguments:
            return ApprovalDecision(
                action="reject",
                result=_approval_reject_result("approved tool arguments mismatch")
            )
        return ApprovalDecision(action="allow")

    return ApprovalDecision(
        action="reject",
        result=_approval_reject_result("approved tool call missing matching approval")
    )


def approval_required(
    *,
    event: dict[str, typing.Any] | None = None,
    meta: dict[str, typing.Any] | None = None
) -> bool:
    """读取远端声明的工具审批策略。"""
    event = event or {}
    meta = meta or {}
    return any(
        _truthy_approval_required(value)
        for value in (
            meta.get("approvalRequired"),
            meta.get("approval_required"),
            event.get("approvalRequired"),
            event.get("approval_required")
        )
    )


def _truthy_approval_required(value: typing.Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "required"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _approval_arguments(
    approval: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    raw = approval.get("arguments", approval.get("args"))
    arguments = dict(raw) if isinstance(raw, dict) else {}
    return _canonical_tool_arguments(arguments)


def _canonical_tool_arguments(
    arguments: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    return typing.cast(dict[str, typing.Any], _normalize_value(arguments))


def _normalize_value(value: typing.Any) -> typing.Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_value(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


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
    noun = _approval_prompt_noun(approval)
    return (
        f"\nWould you like to approve the following {noun}?\n\n"
        f"$ {summary}\n"
    )


def approval_prompt_renderable(
    approval: dict[str, typing.Any]
) -> Group:
    """构造审批提示的终端渲染内容。"""
    noun = _approval_prompt_noun(approval)
    command_line = Text()
    for part in approval_command_parts(approval, newline=False):
        command_line.append(str(part.get("text") or ""), style=part.get("style"))
    return Group(
        Text(f"Would you like to approve the following {noun}?\n", style=APPROVAL_PENDING_STYLE),
        command_line,
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
            {"text": f"{prefix} {index}. ", "style": "bold #A7C7FF" if index == 1 else "bold #9AA9B5"},
            {"text": DECISION_LABELS.get(decision, decision), "style": "bold #E2E8F0" if index == 1 else "bold #9AA9B5"},
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
    noun = _approval_prompt_noun(approval)
    return [
        {"text": f"Would you like to approve the following {noun}?\n\n", "style": APPROVAL_PENDING_STYLE},
        *approval_command_parts(approval, newline=True)
    ]


def approval_command_parts(
    approval: dict[str, typing.Any],
    *,
    newline: bool
) -> list[dict[str, str | None]]:
    """把审批命令行拆成 `$`、工具名和参数片段。"""
    summary = approval_summary(approval)
    tool = str(approval.get("tool") or "").strip()
    parts: list[dict[str, str | None]] = [
        {"text": "$ ", "style": APPROVAL_PROMPT_STYLE}
    ]

    if tool and summary.startswith(tool):
        parts.append({"text": tool, "style": APPROVAL_TOOL_STYLE})
        rest = summary[len(tool):]
        if rest:
            parts.append({"text": rest, "style": APPROVAL_ARG_STYLE})
    else:
        parts.append({"text": summary, "style": APPROVAL_COMMAND_STYLE})

    if newline:
        parts.append({"text": "\n", "style": None})
    return parts


def _approval_prompt_noun(
    approval: dict[str, typing.Any]
) -> str:
    tool = str(approval.get("tool") or "").strip()
    return "command" if tool in {"", "shell_exec"} else "tool action"


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
    input_func: typing.Callable[[str], str] = None,
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
