# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova.stream_events import ToolApprovalRequiredEvent
from mind_nova import const
from .models import (
    ApprovalDecisionValue,
    ExecPolicyAmendmentProposal,
)

DEFAULT_APPROVAL_DECISIONS: tuple[ApprovalDecisionValue, ...] = (
    "accept",
    "acceptForSession",
    "decline"
)

DECISION_LABELS: dict[str, str] = {
    "accept"                        : "Yes, proceed",
    "acceptForSession"              : "Yes, for this session",
    "acceptWithExecpolicyAmendment" : "Yes, and don't ask again for this command prefix",
    "decline"                       : f"No, and tell {const.APP_DESC} what to do differently",
}

DECISION_SHORTCUT_LABELS: dict[str, str] = {
    "accept"                        : "y",
    "acceptForSession"              : "s",
    "acceptWithExecpolicyAmendment" : "p",
    "decline"                       : "n/esc",
}

def approval_from_event(event: ToolApprovalRequiredEvent) -> dict[str, typing.Any]:
    """把直接审批事件字段转换为客户端审批卡载荷。"""
    tool = "write_stdin" if event.kind == "write_stdin" else "exec_command"
    approval: dict[str, typing.Any] = {
        "id": event.approval_id or event.call_id,
        "approval_id": event.approval_id,
        "call_id": event.call_id,
        "tool": tool,
        "kind": event.kind,
        "command": event.command,
        "cwd": event.cwd,
        "reason": event.reason,
        "justification": event.reason,
        "arguments": {
            "command": event.command,
            "cwd": event.cwd,
        },
    }
    if event.proposed_execpolicy_amendment is not None:
        approval["proposed_execpolicy_amendment"] = dict(
            event.proposed_execpolicy_amendment
        )
    if event.available_decisions:
        approval["available_decisions"] = list(event.available_decisions)
    if event.parsed_cmd:
        approval["parsed_cmd"] = list(event.parsed_cmd)
    return approval


def approval_id_from_event(event: ToolApprovalRequiredEvent) -> str:
    """从流式事件中读取审批 ID。"""
    return str(event.approval_id or event.call_id).strip()


def approval_decisions(
    approval: dict[str, typing.Any] | None = None,
) -> list[ApprovalDecisionValue]:
    """按修订提案返回三个可见审批选项。"""
    decisions = list(DEFAULT_APPROVAL_DECISIONS)
    if approval_execpolicy_amendment(approval) is not None:
        decisions[1] = "acceptWithExecpolicyAmendment"
    return decisions


def approval_execpolicy_amendment(
    approval: dict[str, typing.Any] | None,
) -> ExecPolicyAmendmentProposal | None:
    """读取可安全展示和回传的执行策略修订提案。"""
    if not isinstance(approval, dict):
        return None
    raw = approval.get("proposed_execpolicy_amendment")
    if not isinstance(raw, dict):
        return None

    amendment_id = str(raw.get("id") or "").strip()
    display = str(raw.get("display") or "").strip()
    command_prefix = raw.get("command_prefix")
    if (
        not amendment_id
        or not display
        or not isinstance(command_prefix, list)
        or not command_prefix
        or any(not isinstance(value, str) or not value for value in command_prefix)
    ):
        return None
    return ExecPolicyAmendmentProposal(
        id=amendment_id,
        command_prefix=tuple(command_prefix),
        display=display,
    )


def _approval_prompt_noun(
    approval: dict[str, typing.Any]
) -> str:
    """返回审批提示中使用的操作类型名称。"""
    tool = str(approval.get("tool") or "").strip()

    if tool in {"", "shell_command", "exec_command"}:
        return "command"
    if tool == "write_stdin":
        return "session input"

    return "tool action"


def approval_decision_label(
    decision: str,
    approval: dict[str, typing.Any] | None = None,
) -> str:
    """返回客户端定义的审批选项展示文案。"""
    if decision == "acceptWithExecpolicyAmendment":
        amendment = approval_execpolicy_amendment(approval)
        if amendment is not None:
            return (
                "Yes, and don't ask again for commands that start with "
                f"`{amendment.display}`"
            )
    return DECISION_LABELS.get(decision, decision)


def approval_prompt(approval: dict[str, typing.Any] | None) -> str:
    """按工具类型生成客户端审批询问文案。"""
    noun = _approval_prompt_noun(approval or {})
    if noun == "command":
        return "Would you like to run the following command?"
    return f"Would you like to approve the following {noun}?"


if __name__ == '__main__':
    pass
