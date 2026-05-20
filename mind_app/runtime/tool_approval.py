# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from pathlib import Path
from dataclasses import (
    dataclass, field
)
from rich.console import Group
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from mind_core.design import Design
from mind_app.stream_events.approval_trace import approval_summary

InputFunc = typing.Callable[[str], str]

APPROVAL_PROMPT_STYLE = Style.from_dict({
    "approval.prompt": "bold #E2E8F0",
    "approval.choice": "bold #A7C7FF",
})

DANGEROUS_COMMANDS = {
    "rm", "rmdir", "del", "erase", "format", "mkfs", "shutdown",
    "reboot", "halt", "poweroff", "diskpart", "remove-item",
    "ri", "rd"
}


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

    action: typing.Literal["allow", "request", "reject"]
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
        approved: bool
    ) -> None:
        """记录审批请求对应的用户选择。"""
        self.remember_request(call_id=call_id, approval=approval)
        approval_id = str(approval.get("id") or "").strip()
        if approved and approval_id:
            self.approved_by_call_id[call_id] = approval_id
            return None
        self.approved_by_call_id.pop(call_id, None)


def approval_id_from_event(event: dict[str, typing.Any]) -> str:
    """从流式事件中读取审批 ID。"""
    approval = event.get("approval") if isinstance(event.get("approval"), dict) else {}
    return str(approval.get("id") or "").strip()


def is_dangerous_shell_call(name: str, arguments: typing.Any) -> bool:
    """判断工具调用是否匹配需要审批的命令集合。"""
    if name != "shell_exec" or not isinstance(arguments, dict):
        return False
    command = arguments.get("command")
    if not isinstance(command, list) or not command:
        return False
    head = Path(str(command[0])).name.lower()
    return head in DANGEROUS_COMMANDS


def build_approval_required_result(
    *,
    call_id: str,
    arguments: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """构造请求服务端进入审批流程的工具结果。"""
    command = arguments.get("command")
    if not isinstance(command, list):
        command = []
    cwd = str(arguments.get("cwd") or ".")
    head = Path(str(command[0])).name if command else ""
    approval = {
        "id": f"appr_{call_id}",
        "tool": "shell_exec",
        "command": list(command),
        "cwd": cwd,
        "risk": "dangerous",
        "category": "dangerous",
        "reason": f"dangerous command: {head}" if head else "dangerous command",
        "approval_args": {
            "allow_dangerous": True
        }
    }
    return {
        "approval_required": True,
        "approval": approval
    }


def _same_command(left: typing.Any, right: typing.Any) -> bool:
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
    """校验 shell 工具调用对应的审批状态。"""
    if not is_dangerous_shell_call(name, arguments):
        return ApprovalDecision(action="allow")

    call_id = str(event.get("call_id") or "")
    approval_id = str(event.get("approval_id") or "").strip()
    event_approved = bool(event.get("approved"))
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

    result = build_approval_required_result(call_id=call_id, arguments=arguments)
    store.remember_request(call_id=call_id, approval=result["approval"])
    return ApprovalDecision(action="request", result=result)


def _approval_reject_result(message: str) -> dict[str, typing.Any]:
    """构造审批校验未通过时的工具结果。"""
    return {
        "approval_required": False,
        "approval_denied": True,
        "error": message
    }


def approval_prompt_text(approval: dict[str, typing.Any]) -> str:
    """构造审批提示的纯文本内容。"""
    summary = approval_summary(approval)
    return (
        "\nWould you like to run the following command?\n\n"
        f"$ {summary}\n"
    )


def approval_prompt_renderable(approval: dict[str, typing.Any]) -> Group:
    """构造审批提示的终端渲染内容。"""
    summary = approval_summary(approval)
    return Group(
        Text("Would you like to run the following command?\n", style="bold #E2E8F0"),
        Text(f"$ {summary}", style="bold #D7E7FF"),
    )


def approval_prompt_parts(approval: dict[str, typing.Any]) -> list[dict[str, str | None]]:
    """构造审批提示的文本片段。"""
    summary = approval_summary(approval)
    return [
        {"text": "Would you like to run the following command?\n\n", "style": "bold #E2E8F0"},
        {"text": f"$ {summary}\n", "style": "bold #D7E7FF"},
    ]


def _print_approval_prompt(
    approval: dict[str, typing.Any],
    *,
    show_prompt: bool = True
) -> None:
    if show_prompt:
        Design.console.print()
        Design.console.print(approval_prompt_renderable(approval))


async def _read_approval_answer() -> str:
    session: PromptSession[str] = PromptSession()
    with patch_stdout(raw=True):
        return await session.prompt_async(
            HTML("<approval.prompt>Approve?</approval.prompt> <approval.choice>[y/N]</approval.choice> "),
            style=APPROVAL_PROMPT_STYLE
        )


async def _prompt_toolkit_approval(approval: dict[str, typing.Any]) -> str:
    _print_approval_prompt(approval)
    return await _read_approval_answer()


async def prompt_tool_approval(
    approval: dict[str, typing.Any],
    *,
    input_func: InputFunc | None = None,
    show_prompt: bool = True
) -> bool:
    """为审批请求读取一个布尔选择。"""
    try:
        if input_func is not None:
            answer = await asyncio.to_thread(input_func, approval_prompt_text(approval))
        else:
            if not show_prompt:
                answer = await _read_approval_answer()
            else:
                answer = await _prompt_toolkit_approval(approval)
    except (EOFError, KeyboardInterrupt):
        return False
    return str(answer or "").strip().lower() in {"y", "yes"}


if __name__ == '__main__':
    pass
