# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from mind_nova.stream_events import ToolApprovalRequiredEvent
from mind_nova import const
from mind_nova.tool_approval import TOOL_APPROVAL_DECISIONS
from .models import (
    ApprovalDecisionValue,
    ExecPolicyAmendmentProposal
)

DEFAULT_APPROVAL_DECISIONS: tuple[ApprovalDecisionValue, ...] = (
    "accept",
    "acceptForSession",
    "decline"
)

DECISION_LABELS: dict[str, str] = {
    "accept": "Yes, proceed",
    "acceptForSession": "Yes, for this session",
    "acceptWithExecpolicyAmendment": "Yes, and don't ask again for this command prefix",
    "decline": f"No, and tell {const.APP_DESC} what to do differently",
}

DECISION_SHORTCUT_LABELS: dict[str, str] = {
    "accept": "y",
    "acceptForSession": "s",
    "acceptWithExecpolicyAmendment": "p",
    "decline": "n/esc",
}

def approval_from_event(event: ToolApprovalRequiredEvent) -> dict[str, typing.Any]:
    """把直接审批事件字段转换为客户端审批卡载荷。"""
    tool = {
        "write_stdin": "write_stdin",
        "apply_patch": "apply_patch",
    }.get(event.kind, "exec_command")
    raw_cwd = str(event.cwd_raw or event.cwd or ".").strip() or "."

    normalized_cwd = _normalize_approval_cwd(raw_cwd)
    operation = event.patch if event.kind == "apply_patch" else event.command

    approval: dict[str, typing.Any] = {
        "id": event.approval_id or event.call_id,
        "approval_id": event.approval_id,
        "call_id": event.call_id,
        "turn_id": event.turn_id,
        "tool": tool,
        "kind": event.kind,
        "cwd": normalized_cwd,
        "cwd_raw": raw_cwd,
        "reason": event.reason,
        "justification": event.reason,
        "arguments": (
            {"patch": operation, "cwd": normalized_cwd}
            if event.kind == "apply_patch"
            else {"command": operation, "cwd": normalized_cwd}
        ),
    }
    if event.kind == "apply_patch":
        approval["patch"] = operation
    else:
        approval["command"] = operation
    if event.environment_id:
        approval["environment_id"] = event.environment_id
    if event.started_at_ms is not None:
        approval["started_at_ms"] = event.started_at_ms
    if event.plugin_id:
        approval["plugin_id"] = event.plugin_id
    if event.script_path:
        approval["script_path"] = event.script_path
    if event.tty:
        approval["tty"] = True
    if event.additional_permissions is not None:
        approval["additional_permissions"] = dict(event.additional_permissions)
    if event.policy_fingerprint:
        approval["policy_fingerprint"] = event.policy_fingerprint
    if event.patch_scope:
        approval["patch_scope"] = list(event.patch_scope)
    if event.proposed_execpolicy_amendment is not None:
        approval["proposed_execpolicy_amendment"] = dict(
            event.proposed_execpolicy_amendment
        )
    approval["available_decisions"] = list(event.available_decisions)
    if event.parsed_cmd:
        approval["parsed_cmd"] = list(event.parsed_cmd)
    return approval


def approval_reason(approval: dict[str, typing.Any]) -> str:
    """按重试、审批和调用说明的优先级读取最终理由。"""
    for field_name in (
        "retry_reason",
        "approval_reason",
        "justification",
        "reason",
    ):
        value = str(approval.get(field_name) or "").strip()
        if value:
            return value
    return ""


def _normalize_approval_cwd(value: str) -> str:
    """将审批事件中的工作目录规范化为绝对路径。"""
    try:
        return str(Path(value).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return value


def approval_id_from_event(event: ToolApprovalRequiredEvent) -> str:
    """从流式事件中读取审批 ID。"""
    return str(event.approval_id or event.call_id).strip()


def approval_decisions(
    approval: dict[str, typing.Any] | None = None
) -> list[ApprovalDecisionValue]:
    """返回审批请求声明的可用决策集合。"""
    if isinstance(approval, dict) and "available_decisions" in approval:
        raw_decisions = approval.get("available_decisions")
        if not isinstance(raw_decisions, (list, tuple)):
            raise ValueError("available_decisions must be an array")
        decisions: list[ApprovalDecisionValue] = []

        seen: set[str] = set()

        for raw_decision in raw_decisions:
            decision = str(raw_decision or "").strip()
            if not decision:
                raise ValueError("available_decisions contains an empty item")
            if decision in seen:
                continue
            if decision not in TOOL_APPROVAL_DECISIONS:
                raise ValueError(f"unsupported approval decision: {decision}")
            if decision == "cancel":
                continue
            seen.add(decision)
            decisions.append(typing.cast(ApprovalDecisionValue, decision))
        if approval_execpolicy_amendment(approval) is None:
            decisions = [
                decision
                for decision in decisions
                if decision != "acceptWithExecpolicyAmendment"
            ]
        return decisions

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

    amendment_id   = str(raw.get("id") or "").strip()
    display        = str(raw.get("display") or "").strip()
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


def _approval_prompt_noun(approval: dict[str, typing.Any]) -> str:
    """返回审批提示中使用的操作类型名称。"""
    tool = str(approval.get("tool") or "").strip()

    if tool in {"", "shell_command", "exec_command"}:
        return "command"
    if tool == "write_stdin":
        return "session input"
    if tool == "apply_patch":
        return "patch"

    return "tool action"


def approval_decision_label(
    decision: str,
    approval: dict[str, typing.Any] | None = None
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
    if noun == "patch":
        return "Would you like to apply the following patch?"
    return f"Would you like to approve the following {noun}?"


if __name__ == '__main__':
    pass
