# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from dataclasses import (
    dataclass,
    field
)
from mind_nova.requests.permissions import ApprovalPolicy
from mind_nova.stream_events import (
    ToolApprovalRequiredEvent,
    ToolCallEvent
)
from mind_nova import const
from mind_nova.tool_approval import TOOL_APPROVAL_ACCEPT_DECISIONS
from .models import (
    ApprovalDecision,
    ApprovalDecisionValue,
    ApprovalRecord,
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

SHELL_TOOL_NAMES = {
    "shell_command",
    "exec_command",
    "write_stdin"
}

SHOW_APPROVAL_TIMER = True


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

        tool = str(approval.get("tool") or "shell_command").strip() or "shell_command"

        self.by_call_id[call_id] = ApprovalRecord(
            approval_id=approval_id,
            call_id=call_id,
            tool=tool,
            arguments=_approval_arguments(approval, tool=tool)
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

        if decision in TOOL_APPROVAL_ACCEPT_DECISIONS and approval_id:
            self.approved_by_call_id[call_id] = approval_id
            return None
        self.approved_by_call_id.pop(call_id, None)


def approval_from_event(event: ToolApprovalRequiredEvent) -> dict[str, typing.Any]:
    """把服务端审批事件归一化为审批卡和审批记录使用的 approval。"""
    approval = dict(event.approval)
    raw_meta = event.meta or {}

    meta_approval = raw_meta.get("approval") if isinstance(raw_meta.get("approval"), dict) else {}
    for key, value in meta_approval.items():
        approval.setdefault(key, value)

    if "arguments" not in approval and event.arguments:
        approval["arguments"] = dict(event.arguments)
    if "arguments" not in approval and event.execution:
        canonical = (
            event.execution.get("canonicalArguments")
            or event.execution.get("canonical_arguments")
        )
        if isinstance(canonical, dict):
            approval["arguments"] = canonical

    if "tool" not in approval:
        approval["tool"] = event.name

    if "environment" not in approval and event.execution:
        target = str(event.execution.get("target") or "").strip()
        if target:
            approval["environment"] = target

    return approval


def approval_id_from_event(event: ToolApprovalRequiredEvent) -> str:
    """从流式事件中读取审批 ID。"""
    return str(event.approval.get("id") or "").strip()


def validate_tool_approval(
    *,
    event: ToolCallEvent,
    name: str,
    arguments: dict[str, typing.Any],
    store: ApprovalStore,
    meta: dict[str, typing.Any] | None = None,
    local_meta: dict[str, typing.Any] | None = None,
    approval_policy: ApprovalPolicy | None = None
) -> ApprovalDecision:
    """校验服务端已批准工具调用的审批元数据。"""
    tool_name = str(name or "").strip()
    if not tool_name:
        return ApprovalDecision(action="allow")

    call_id        = event.call_id
    approval_id    = event.approval_id
    event_approved = event.approved

    effective_meta = (
        {**local_meta, **meta}
        if isinstance(local_meta, dict) and isinstance(meta, dict)
        else meta if isinstance(meta, dict)
        else local_meta if isinstance(local_meta, dict)
        else None
    )

    if not event_approved and not approval_id:
        if approval_required(event=event, meta=effective_meta):
            if approval_policy == "never":
                return ApprovalDecision(
                    action="reject",
                    result=_approval_reject_result(
                        "approval disabled by approval policy"
                    ),
                )
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
        if _canonical_tool_arguments(tool_name, arguments) != record.arguments:
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
    event: ToolCallEvent | None = None,
    meta: dict[str, typing.Any] | None = None
) -> bool:
    """读取远端声明的工具审批策略。"""
    meta = meta or {}

    return bool(event and event.approval_required) or any(
        _truthy_approval_required(value)
        for value in (
            meta.get("approvalRequired"),
            meta.get("approval_required"),
        )
    )


def _truthy_approval_required(value: typing.Any) -> bool:
    """判断审批标记值是否表示需要审批。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "required"}
    if isinstance(value, (int, float)):
        return bool(value)

    return False


def approval_expired(approval: dict[str, typing.Any] | None) -> bool:
    """根据 expires_at_ms 判断审批是否已过期。"""
    remaining = approval_remaining_sec(approval)
    return remaining is not None and remaining <= 0


def approval_remaining_sec(approval: dict[str, typing.Any] | None) -> float | None:
    """返回审批剩余秒数；缺少过期时间时返回 None。"""
    expires_at_ms = _approval_expires_at_ms(approval)
    if expires_at_ms is None:
        return None
    return (expires_at_ms - int(time.time() * 1000)) / 1000.0


def approval_expiry_label(approval: dict[str, typing.Any] | None) -> str:
    """生成审批过期倒计时文案。"""
    if not approval_show_timer():
        return ""

    remaining = approval_remaining_sec(approval)
    if remaining is None:
        return ""
    if remaining <= 0:
        return "Approval expired"

    total_sec = max(1, int(remaining + 0.999))

    minutes, seconds = divmod(total_sec, 60)
    if minutes:
        return f"Expires in {minutes}m {seconds:02d}s"
    return f"Expires in {seconds}s"


def approval_show_timer() -> bool:
    """返回客户端审批倒计时展示配置。"""
    return SHOW_APPROVAL_TIMER


def _approval_expires_at_ms(approval: dict[str, typing.Any] | None) -> int | None:
    """读取审批过期时间毫秒时间戳。"""
    if not isinstance(approval, dict):
        return None
    raw = approval.get("expires_at_ms") or approval.get("expiresAtMs")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _approval_arguments(
    approval: dict[str, typing.Any],
    *,
    tool: str
) -> dict[str, typing.Any]:
    """读取并规范化审批请求参数。"""
    raw       = approval.get("arguments", approval.get("args"))
    arguments = dict(raw) if isinstance(raw, dict) else _approval_argument_fallback(approval, tool=tool)

    return _canonical_tool_arguments(tool, arguments)


def _canonical_tool_arguments(
    tool: str,
    arguments: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """返回可稳定比较的工具参数。"""
    normalized_tool = str(tool or "").strip()
    if normalized_tool == "shell_command":
        return _normalize_shell_command_arguments(arguments)
    if normalized_tool == "exec_command":
        return _normalize_exec_command_arguments(arguments)
    if normalized_tool == "write_stdin":
        return _normalize_write_stdin_arguments(arguments)

    normalized = _normalize_value(arguments)
    return normalized if isinstance(normalized, dict) else {}


def _approval_argument_fallback(
    approval: dict[str, typing.Any],
    *,
    tool: str
) -> dict[str, typing.Any]:
    """兼容审批事件只携带 command 摘要而缺少 arguments 的情况。"""
    normalized_tool = str(tool or "").strip()
    if normalized_tool not in SHELL_TOOL_NAMES:
        return {}

    command = str(approval.get("command") or "").strip()
    if not command:
        return {}

    item: dict[str, typing.Any] = {"command": command}
    if "cwd" in approval:
        item["cwd"] = approval.get("cwd")
    if "timeout_sec" in approval:
        item["timeout_sec"] = approval.get("timeout_sec")

    return item


def _normalize_shell_command_arguments(
    arguments: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """规范化单条 shell_command 参数。"""
    return _normalize_shell_command_item(arguments)


def _normalize_shell_command_item(
    value: typing.Any
) -> dict[str, typing.Any]:
    """规范化单条 shell_command 参数。"""
    item = value if isinstance(value, dict) else {}
    return {
        "command"     : str(item.get("command") or ""),
        "cwd"         : str(item.get("cwd") or "."),
        "timeout_sec" : int(item.get("timeout_sec") or 60)
    }


def _normalize_exec_command_arguments(
    arguments: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """规范化 exec_command 参数。"""
    item = arguments if isinstance(arguments, dict) else {}

    return {
        "command"          : str(item.get("command") or ""),
        "cwd"              : str(item.get("cwd") or "."),
        "yield_time_ms"    : _int_default(item.get("yield_time_ms"), 1000),
        "max_output_chars" : int(item.get("max_output_chars") or 24000),
        "timeout_sec"      : int(item.get("timeout_sec") or 1800),
        "idle_timeout_sec" : int(item.get("idle_timeout_sec") or 300)
    }


def _normalize_write_stdin_arguments(
    arguments: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """规范化 write_stdin 参数。"""
    item = arguments if isinstance(arguments, dict) else {}

    return {
        "session_id"       : str(item.get("session_id") or ""),
        "stdin"            : str(item.get("stdin") or ""),
        "wait_ms"          : _int_default(item.get("wait_ms"), 1000),
        "max_output_chars" : int(item.get("max_output_chars") or 12000),
        "control"          : str(item.get("control") or "none")
    }


def _int_default(value: typing.Any, default: int) -> int:
    """转换整数并保留有效的 0 值。"""
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _normalize_value(value: typing.Any) -> typing.Any:
    """递归规范化审批参数中的值。"""
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
