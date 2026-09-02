# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path

from agent.application.approvals.amendments import (
    ExecPolicyAmendmentProposal,
    approval_execpolicy_amendment,
)
from metadata import const
from protocol.schema.stream_events import ToolApprovalRequiredEvent
from protocol.schema.tool_approval import (
    TOOL_APPROVAL_DECISIONS,
    TOOL_APPROVAL_DECISIONS_BY_KIND,
)
from .models import (
    ApprovalDecisionValue,
    normalize_approval_decision,
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
    "applyNetworkPolicyAmendment": "Yes, and allow this host in the future",
    "grantForTurn": "Yes, grant these permissions for this turn",
    "grantForTurnWithStrictAutoReview": "Yes, grant for this turn with strict auto review",
    "grantForSession": "Yes, grant these permissions for this session",
    "decline": f"No, and tell {const.APP_DESC} what to do differently",
}

DECISION_SHORTCUT_LABELS: dict[str, str] = {
    "accept": "y",
    "acceptForSession": "s",
    "acceptWithExecpolicyAmendment": "p",
    "applyNetworkPolicyAmendment": "p",
    "grantForTurn": "y",
    "grantForTurnWithStrictAutoReview": "r",
    "grantForSession": "s",
    "decline": "n/esc",
}


def approval_from_event(event: ToolApprovalRequiredEvent) -> dict[str, typing.Any]:
    """把直接审批事件字段转换为客户端审批卡载荷。"""
    tool = {
        "command": "exec_command",
        "network_access": "exec_command",
        "write_stdin": "write_stdin",
        "apply_patch": "apply_patch",
    }.get(event.kind, "")
    raw_cwd = str(event.cwd_raw or event.cwd or "").strip()
    normalized_cwd = _normalize_approval_cwd(raw_cwd) if raw_cwd else ""
    if event.kind == "apply_patch":
        operation = event.patch
        arguments: dict[str, typing.Any] = {
            "patch": operation,
            "cwd": normalized_cwd,
        }
    elif event.kind == "write_stdin":
        operation = event.input
        arguments = {
            "input": event.input,
            "control": event.control,
            "session_id": event.session_id,
        }
    elif event.kind == "request_permissions":
        operation = ""
        arguments = {"permissions": dict(event.permissions or {})}
    elif event.kind == "mcp_tool_call":
        operation = ""
        arguments = event.arguments
    else:
        operation = event.command
        arguments = {"command": operation, "cwd": normalized_cwd}

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
        "arguments": arguments,
    }
    if event.kind == "apply_patch":
        approval["patch"] = operation
        approval["files"] = list(event.files or event.patch_scope)
        if event.permissions_preapproved is not None:
            approval["permissions_preapproved"] = event.permissions_preapproved
    elif event.kind in {"command", "network_access"}:
        approval["command"] = operation
    elif event.kind == "write_stdin":
        approval["input"] = event.input
        approval["control"] = event.control
        approval["session_id"] = event.session_id
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
    if event.kind == "command":
        approval["sandbox_permissions"] = event.sandbox_permissions
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
    if event.proposed_network_policy_amendment is not None:
        approval["proposed_network_policy_amendment"] = dict(
            event.proposed_network_policy_amendment
        )
    if event.target:
        approval["target"] = event.target
    if event.host:
        approval["host"] = event.host
    if event.protocol:
        approval["protocol"] = event.protocol
    if event.port is not None:
        approval["port"] = event.port
    if event.permissions is not None:
        approval["permissions"] = dict(event.permissions)
    if event.scope is not None:
        approval["scope"] = event.scope
    if event.strict_auto_review is not None:
        approval["strict_auto_review"] = event.strict_auto_review
    if event.server:
        approval["server"] = event.server
    if event.tool_name:
        approval["tool_name"] = event.tool_name
    if event.mcp_request_id:
        approval["mcp_request_id"] = event.mcp_request_id
    for field_name in (
            "connector_id", "connector_name", "connector_description",
            "connected_account_email", "tool_title", "tool_description",
    ):
        value = getattr(event, field_name)
        if value:
            approval[field_name] = value
    if event.annotations is not None:
        approval["annotations"] = dict(event.annotations)
    if event.status != "pending":
        approval["status"] = event.status
    if event.ack is not None:
        approval["ack"] = dict(event.ack)
    approval["available_decisions"] = list(event.available_decisions)
    if event.parsed_cmd:
        approval["parsed_cmd"] = list(event.parsed_cmd)
    return approval


def approval_from_snapshot(
    item: typing.Mapping[str, typing.Any],
) -> dict[str, typing.Any]:
    """把服务端快照中的审批记录转换为事件同构载荷。"""
    approval = dict(item)
    approval_id = str(approval.get("approval_id") or "").strip()
    call_id = str(approval.get("call_id") or "").strip()
    turn_id = str(approval.get("turn_id") or "").strip()
    kind = str(approval.get("kind") or "command").strip()

    if kind == "apply_patch":
        tool = "apply_patch"
        operation_field = "patch"
    elif kind == "write_stdin":
        tool = "write_stdin"
        operation_field = "input"
    elif kind == "network_access":
        tool = "exec_command"
        operation_field = "command"
    elif kind in {"request_permissions", "mcp_tool_call"}:
        tool = ""
        operation_field = "command"
    else:
        tool = "exec_command"
        operation_field = "command"

    operation = approval.get(operation_field)

    raw_cwd_value = (
        approval.get("cwd_raw")
        or approval.get("cwd")
    )
    raw_cwd = str(raw_cwd_value or "").strip()
    normalized_cwd = _normalize_approval_cwd(raw_cwd) if raw_cwd else ""

    if kind == "request_permissions":
        arguments = {"permissions": dict(approval.get("permissions") or {})}
    elif kind == "mcp_tool_call":
        arguments = approval.get("arguments")
    elif kind == "write_stdin":
        arguments = {
            "input": str(approval.get("input") or ""),
            "control": str(approval.get("control") or "none"),
            "session_id": str(approval.get("session_id") or ""),
        }
    else:
        arguments = {
            operation_field: operation,
            "cwd": normalized_cwd,
        }

    approval.update({
        "id": approval_id or call_id,
        "approval_id": approval_id,
        "call_id": call_id,
        "turn_id": turn_id,
        "tool": tool,
        "kind": kind,
        "cwd": normalized_cwd,
        "cwd_raw": raw_cwd,
        "arguments": arguments,
    })
    if operation and operation_field not in approval:
        approval[operation_field] = operation
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

        kind = str(approval.get("kind") or "command").strip()

        allowed_for_kind = TOOL_APPROVAL_DECISIONS_BY_KIND.get(kind)
        if allowed_for_kind is None:
            raise ValueError(f"unsupported approval kind: {kind}")

        for raw_decision in raw_decisions:
            decision = str(raw_decision or "").strip()
            if not decision:
                raise ValueError("available_decisions contains an empty item")
            if decision in seen:
                continue
            if decision not in TOOL_APPROVAL_DECISIONS:
                raise ValueError(f"unsupported approval decision: {decision}")
            if decision not in allowed_for_kind:
                raise ValueError(
                    f"approval decision is invalid for kind: {kind}"
                )
            if decision == "cancel":
                continue
            seen.add(decision)
            decisions.append(normalize_approval_decision(decision))
        if approval_execpolicy_amendment(approval) is None:
            decisions = [
                decision
                for decision in decisions
                if decision != "acceptWithExecpolicyAmendment"
            ]
        return decisions

    kind = str(approval.get("kind") or "command") if isinstance(approval, dict) else "command"
    kind_defaults: dict[str, list[ApprovalDecisionValue]] = {
        "request_permissions": [
            "grantForTurn",
            "grantForTurnWithStrictAutoReview",
            "grantForSession",
            "decline",
        ],
        "mcp_tool_call": ["accept", "decline"],
    }
    decisions = list(kind_defaults.get(kind, DEFAULT_APPROVAL_DECISIONS))
    if kind == "command" and approval_execpolicy_amendment(approval) is not None:
        decisions[1] = "acceptWithExecpolicyAmendment"
    return decisions


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
    approval: dict[str, typing.Any] | None = None,
    *,
    amendment: ExecPolicyAmendmentProposal | None = None
) -> str:
    """返回客户端定义的审批选项展示文案。"""
    if decision == "acceptWithExecpolicyAmendment":
        amendment = amendment or approval_execpolicy_amendment(approval)
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
        return "Would you like to make the following edits?"
    return f"Would you like to approve the following {noun}?"


if __name__ == '__main__':
    pass
