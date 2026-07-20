# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from dataclasses import (
    dataclass,
    field
)
from mind_app.stream_events.approval_trace import approval_summary
from mind_nova import const
from .models import (
    ApprovalDecision,
    ApprovalDecisionValue,
    ApprovalRecord
)

DEFAULT_APPROVAL_DECISIONS: tuple[ApprovalDecisionValue, ...] = (
    "accept",
    "decline"
)

DECISION_LABELS: dict[str, str] = {
    "accept"           : "Yes, proceed",
    "acceptForSession" : "Yes, for this session",
    "decline"          : f"No, and tell {const.APP_DESC} what to do differently"
}

DECISION_SHORTCUT_LABELS: dict[str, str] = {
    "accept"           : "y",
    "acceptForSession" : "s",
    "decline"          : "n/esc"
}

SHELL_TOOL_NAMES = {
    "shell_command",
    "exec_command",
    "write_stdin"
}


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

        if decision in {"accept", "acceptForSession"} and approval_id:
            self.approved_by_call_id[call_id] = approval_id
            return None
        self.approved_by_call_id.pop(call_id, None)


def approval_from_event(event: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """把服务端审批事件归一化为审批卡和审批记录使用的 approval。"""
    raw_approval = event.get("approval") if isinstance(event.get("approval"), dict) else {}
    approval     = dict(raw_approval)
    raw_meta     = event.get("meta") if isinstance(event.get("meta"), dict) else {}

    meta_approval = raw_meta.get("approval") if isinstance(raw_meta.get("approval"), dict) else {}
    for key, value in meta_approval.items():
        approval.setdefault(key, value)

    if "arguments" not in approval and isinstance(event.get("arguments"), dict):
        approval["arguments"] = event.get("arguments")
    if "arguments" not in approval and isinstance(event.get("execution"), dict):
        canonical = (
            event["execution"].get("canonicalArguments")
            or event["execution"].get("canonical_arguments")
        )
        if isinstance(canonical, dict):
            approval["arguments"] = canonical

    if "tool" not in approval:
        approval["tool"] = str(event.get("tool") or event.get("name") or "").strip()

    return approval


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
    local_meta: dict[str, typing.Any] | None = None
) -> ApprovalDecision:
    """校验服务端已批准工具调用的审批元数据。"""
    tool_name = str(name or "").strip()
    if not tool_name:
        return ApprovalDecision(action="allow")

    call_id        = str(event.get("call_id") or "")
    approval_id    = str(event.get("approval_id") or "").strip()
    event_approved = bool(event.get("approved"))

    effective_meta = (
        {**local_meta, **meta}
        if isinstance(local_meta, dict) and isinstance(meta, dict)
        else meta if isinstance(meta, dict)
        else local_meta if isinstance(local_meta, dict)
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
    event: dict[str, typing.Any] | None = None,
    meta: dict[str, typing.Any] | None = None
) -> bool:
    """读取远端声明的工具审批策略。"""
    event = event or {}
    meta  = meta or {}

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
    if not approval_show_timer(approval):
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


def approval_show_timer(approval: dict[str, typing.Any] | None) -> bool:
    """读取审批倒计时展示配置。"""
    return _approval_bool(approval, "show_timer", default=True)


def _approval_bool(
    approval: dict[str, typing.Any] | None,
    key: str,
    *,
    default: bool
) -> bool:
    """读取审批数据中的布尔配置。"""
    if not isinstance(approval, dict) or key not in approval:
        return default

    value = approval.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)

    return default


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


def approval_prompt_text(
    approval: dict[str, typing.Any]
) -> str:
    """构造审批提示的纯文本内容。"""
    summary = approval_summary(approval)
    prompt  = approval_prompt(approval)

    return (
        f"\n{prompt}\n\n"
        f"$ {summary}\n"
    )


def approval_decisions(
    approval: dict[str, typing.Any]
) -> list[ApprovalDecisionValue]:
    """读取可用审批选项，并补齐默认值。"""
    raw    = approval.get("availableDecisions", approval.get("available_decisions"))
    values = raw if isinstance(raw, list) else list(DEFAULT_APPROVAL_DECISIONS)

    out: list[ApprovalDecisionValue] = []

    for value in values:
        normalized = _normalize_decision(value)
        if normalized is not None and normalized not in out:
            out.append(normalized)
    if "decline" not in out:
        out.append("decline")

    return out or list(DEFAULT_APPROVAL_DECISIONS)


def approval_choice_text(
    approval: dict[str, typing.Any]
) -> str:
    """构造审批选项纯文本。"""
    lines = []
    for index, decision in enumerate(approval_decisions(approval), start=1):
        prefix = "›" if index == 1 else " "
        lines.append(f"{prefix} {index}. {_decision_display_label(decision, approval=approval)}")
    return "\n".join(lines) + "\n"


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


def _normalize_decision(value: typing.Any) -> ApprovalDecisionValue | None:
    """将输入的审批选项值转换为内部枚举。"""
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
        "no"                 : "decline"
    }

    return aliases.get(text.lower())


def _decision_display_label(
    decision: str,
    *,
    approval: dict[str, typing.Any] | None = None
) -> str:
    """返回审批选项的展示文案，包含可用快捷键提示。"""
    label    = approval_decision_label(approval, decision)
    shortcut = DECISION_SHORTCUT_LABELS.get(decision, "")
    return f"{label} ({shortcut})" if shortcut else label


def approval_decision_label(
    approval: dict[str, typing.Any] | None,
    decision: str
) -> str:
    """读取审批选项展示文案。"""
    labels: dict = {}
    if isinstance(approval, dict):
        raw = approval.get("decision_labels", approval.get("decisionLabels"))
        labels = raw if isinstance(raw, dict) else {}

    custom = str(labels.get(decision) or "").strip()
    return custom or DECISION_LABELS.get(decision, decision)


def approval_prompt(approval: dict[str, typing.Any] | None) -> str:
    """读取审批询问文案，缺省时按命令数量生成。"""
    if isinstance(approval, dict):
        prompt = str(approval.get("prompt") or "").strip()
        if prompt:
            return prompt

    noun = _approval_prompt_noun(approval or {})
    return f"Would you like to approve the following {noun}?"


if __name__ == '__main__':
    pass
