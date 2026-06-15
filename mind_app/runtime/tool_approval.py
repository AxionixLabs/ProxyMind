# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import shutil
import typing
import asyncio
import contextlib
from dataclasses import (
    dataclass, field
)
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from mind_app.stream_events.approval_trace import (
    approval_shell_commands,
    approval_summary
)
from mind_app.stream_events.command_preview import command_text
from mind_app.stream_events.compact_rule import (
    COMPACT_RULE_PADDING,
    COMPACT_RULE_TERMINAL_MARGIN
)
from mind_app.stream_events.tool_traces.command_parts import render_command_parts
from mind_app.stream_events.tool_traces.common import (
    COMMAND_FLAG_STYLE,
    COMMAND_HEAD_STYLE,
    COMMAND_NUMBER_STYLE,
    COMMAND_OPERATOR_STYLE,
    COMMAND_PATH_STYLE,
    COMMAND_STRING_STYLE,
    COMMAND_STYLE
)
from mind_nova import const

ApprovalDecisionValue = typing.Literal[
    "accept",
    "acceptForSession",
    "decline",
    "cancel",
    "expired"
]

DEFAULT_APPROVAL_DECISIONS: tuple[ApprovalDecisionValue, ...] = (
    "accept",
    "decline"
)

DECISION_LABELS: dict[str, str] = {
    "accept"           : "Yes, proceed",
    "acceptForSession" : "Yes, for this session",
    "decline"          : f"No, and tell {const.APP_DESC} what to do differently",
    "cancel"           : "Cancel"
}

DECISION_SHORTCUT_LABELS: dict[str, str] = {
    "accept"           : "y",
    "acceptForSession" : "s",
    "decline"          : "n",
    "cancel"           : "esc"
}

APPROVAL_MENU_STYLE = Style.from_dict({
    "radio-list"        : "",
    "radio"             : "#8B96A3",
    "radio-selected"    : "bold #4DE3FF",
    "radio-checked"     : "bold #C7F7FF",
    "radio-number"      : "#8B96A3",
    "shortcut"          : "dim #6F7B88",
    "shortcut-selected" : "bold #C7F7FF",
    "approval-pending"  : "bold #4DE3FF",
    "approval-prompt"   : "#7D8A98",
    "approval-tool"     : "bold #8BD3FF",
    "approval-arg"      : "#B7C5D3",
    "approval-command"  : "#D8E3EE",
    "approval-border"   : "#667380",
    "approval-preview"  : "dim #8896A5",
    "command"           : COMMAND_STYLE,
    "command-head"      : COMMAND_HEAD_STYLE,
    "command-flag"      : COMMAND_FLAG_STYLE,
    "command-path"      : COMMAND_PATH_STYLE,
    "command-string"    : COMMAND_STRING_STYLE,
    "command-number"    : COMMAND_NUMBER_STYLE,
    "command-operator"  : COMMAND_OPERATOR_STYLE
})

APPROVAL_MENU_PADDING             = COMPACT_RULE_PADDING
APPROVAL_MENU_TERMINAL_MARGIN     = COMPACT_RULE_TERMINAL_MARGIN
APPROVAL_MENU_TITLE_MIN_RULE      = 4
APPROVAL_MENU_CONTINUATION_INDENT = 2
APPROVAL_MENU_FIXED_WIDTH         = 104
APPROVAL_MENU_MAX_COMMAND_WIDTH   = APPROVAL_MENU_FIXED_WIDTH - APPROVAL_MENU_PADDING


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
    if str(tool or "").strip() == "shell_command":
        return _normalize_shell_command_arguments(arguments)

    normalized = _normalize_value(arguments)
    return normalized if isinstance(normalized, dict) else {}


def _approval_argument_fallback(
    approval: dict[str, typing.Any],
    *,
    tool: str
) -> dict[str, typing.Any]:
    """兼容审批事件只携带 command/items 摘要而缺少 arguments 的情况。"""
    if str(tool or "").strip() != "shell_command":
        return {}

    raw_items = approval.get("items")
    if isinstance(raw_items, list):
        return {"items": raw_items}

    command = str(approval.get("command") or "").strip()
    if not command:
        return {}

    item: dict[str, typing.Any] = {"command": command}
    if "cwd" in approval:
        item["cwd"] = approval.get("cwd")
    if "timeout_sec" in approval:
        item["timeout_sec"] = approval.get("timeout_sec")

    return {"items": [item]}


def _normalize_shell_command_arguments(
    arguments: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """规范化批量 shell_command 参数。"""
    if not isinstance(arguments, dict):
        return {"items": []}

    raw_items = arguments.get("items")
    if isinstance(raw_items, list):
        return {"items": [_normalize_shell_command_item(item) for item in raw_items]}

    if "command" in arguments:
        return {"items": [_normalize_shell_command_item(arguments)]}

    return {"items": []}


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
    return "command" if tool in {"", "shell_command"} else "tool action"


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
        "no"                 : "decline",
        "cancel"             : "cancel"
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
    labels = {}
    if isinstance(approval, dict):
        raw = approval.get("decision_labels", approval.get("decisionLabels"))
        labels = raw if isinstance(raw, dict) else {}

    custom = str(labels.get(decision) or "").strip()
    return custom or DECISION_LABELS.get(decision, decision)


def _decision_display_prompt_parts(
    decision: str,
    *,
    approval: dict[str, typing.Any] | None,
    style: str,
    shortcut_style: str
) -> list[tuple[str, str]]:
    """返回 prompt_toolkit 菜单选项的分段展示文案。"""
    label    = approval_decision_label(approval, decision)
    shortcut = DECISION_SHORTCUT_LABELS.get(decision, "")
    parts    = [(style, label)]

    if shortcut:
        parts.extend(
            [(style, " ("), (shortcut_style, shortcut), (style, ")")]
        )

    return parts


def approval_menu_content_lines(
    decisions: list[ApprovalDecisionValue],
    *,
    approval: dict[str, typing.Any] | None,
    selected_index: int = 0
) -> list[list[tuple[str, str]]]:
    """生成审批菜单未加边框的语义内容行。"""
    lines: list[list[tuple[str, str]]] = []
    if approval is not None:
        lines.extend(_approval_question_lines(approval))
        lines.extend(_approval_command_lines(approval))
        if expiry_label := approval_expiry_label(approval):
            lines.append([("class:approval-preview", expiry_label)])
        lines.append([])

    for index, decision in enumerate(decisions, start=1):
        active         = index - 1 == selected_index
        prefix_style   = "class:radio"
        label_style    = "class:radio-selected" if active else "class:radio"
        prefix         = "›" if active else " "
        shortcut_style = "class:shortcut-selected" if active else "class:shortcut"

        line: list[tuple[str, str]] = [(prefix_style, f"{prefix} {index}. ")]
        line.extend(
            _decision_display_prompt_parts(
                decision,
                approval=approval,
                style=label_style,
                shortcut_style=shortcut_style
            )
        )
        lines.append(line)

    return lines


def render_bordered_approval_menu(
    lines: list[list[tuple[str, str]]],
    *,
    max_width: int | None = None,
    max_height: int | None = None,
    padding: int = APPROVAL_MENU_PADDING,
    title: str | None = "Review command"
) -> list[tuple[str, str]]:
    """把审批菜单内容行渲染为顶部规则线和缩进内容。"""
    horizontal_width = _approval_rule_width(max_width=max_width)
    content_width    = max(1, horizontal_width - padding)

    wrapped_lines = _wrap_approval_menu_lines(
        lines,
        max_width=content_width,
        max_height=max_height
    )

    parts: list[tuple[str, str]] = [
        *_approval_top_border_parts(horizontal_width, title=title),
        ("class:approval-border", "\n"),
        ("", "\n")
    ]

    for line in wrapped_lines:
        if not line:
            parts.append(("", "\n"))
            continue
        parts.append(("", " " * padding))
        parts.extend(line)
        parts.append(("", "\n"))

    parts.append(("", "\n"))
    return parts


def approval_menu_content_width(
    lines: list[list[tuple[str, str]]],
    *,
    max_width: int | None = None
) -> int:
    """返回审批菜单内容区宽度。"""
    if not lines:
        return max(1, _approval_rule_width(max_width=max_width) - APPROVAL_MENU_PADDING)

    natural = max(approval_menu_line_width(line) for line in lines)

    return min(
        APPROVAL_MENU_MAX_COMMAND_WIDTH, max(1, natural)
    )


def approval_menu_command_width(
    lines: list[list[tuple[str, str]]],
    *,
    max_width: int | None = None
) -> int:
    """返回审批菜单命令区域宽度。"""
    _ = max_width
    return approval_menu_content_width(lines, max_width=max_width)


def approval_menu_rule_width(
    lines: list[list[tuple[str, str]]],
    *,
    max_width: int | None = None
) -> int:
    """返回审批菜单边框宽度。"""
    _ = lines
    return _approval_rule_width(max_width=max_width)


def approval_title(approval: dict[str, typing.Any] | None) -> str:
    """读取审批卡标题。"""
    if not isinstance(approval, dict):
        return "Review command"
    return str(approval.get("title") or "Review command").strip() or "Review command"


def approval_prompt(approval: dict[str, typing.Any] | None) -> str:
    """读取审批询问文案，缺省时按命令数量生成。"""
    if isinstance(approval, dict):
        prompt = str(approval.get("prompt") or "").strip()
        if prompt:
            return prompt

    noun = _approval_prompt_noun(approval or {})
    return f"Would you like to approve the following {noun}?"


def _approval_rule_width(*, max_width: int | None) -> int:
    """按固定宽度策略计算审批卡规则线宽度。"""
    if max_width is None:
        return APPROVAL_MENU_FIXED_WIDTH

    available = max(1, int(max_width) - APPROVAL_MENU_TERMINAL_MARGIN)
    return min(APPROVAL_MENU_FIXED_WIDTH, available)


def approval_menu_line_width(line: list[tuple[str, str]]) -> int:
    """返回 prompt_toolkit 文本片段的终端显示宽度。"""
    return sum(get_cwidth(text) for _style, text in line)


def approval_menu_plain_text(parts: list[tuple[str, str]]) -> str:
    """把 prompt_toolkit 片段转为纯文本。"""
    return "".join(text for _style, text in parts)


def _approval_top_border_parts(
    width: int,
    *,
    title: str | None
) -> list[tuple[str, str]]:
    """生成包含居中标题的审批卡上边框。"""
    title_text = str(title or "").strip()
    if not title_text:
        return [("class:approval-border", "─" * width)]

    decorated       = f" {title_text} "
    max_title_width = max(1, width - APPROVAL_MENU_TITLE_MIN_RULE * 2)

    title_width = get_cwidth(decorated)
    if title_width > max_title_width:
        decorated   = _clip_display_width(decorated, max_width=max_title_width)
        title_width = get_cwidth(decorated)

    if title_width >= width:
        return [("class:approval-pending", decorated)]

    left  = (width - title_width) // 2
    right = width - title_width - left

    return [
        ("class:approval-border", "─" * left),
        ("class:approval-pending", decorated),
        ("class:approval-border", "─" * right)
    ]


def _clip_display_width(text: str, *, max_width: int) -> str:
    """按显示宽度截断文本。"""
    limit = max(1, int(max_width or 1))
    if get_cwidth(text) <= limit:
        return text
    if limit <= 1:
        return "…"

    out: str  = ""
    used: int = 0

    for char in text:
        char_width = get_cwidth(char)
        if used + char_width > limit - 1:
            break
        out += char
        used += char_width

    return f"{out}…"


def _approval_question_lines(
    approval: dict[str, typing.Any]
) -> list[list[tuple[str, str]]]:
    """生成审批询问文案。"""
    return [
        [("class:approval-pending", approval_prompt(approval))], []
    ]


def _approval_command_lines(
    approval: dict[str, typing.Any]
) -> list[list[tuple[str, str]]]:
    """生成审批命令区域。"""
    commands = _approval_raw_commands(approval)
    if len(commands) > 1:
        return _approval_batch_command_lines(commands)

    if commands:
        return _approval_single_command_lines(commands[0])

    line: list[tuple[str, str]] = [("class:approval-prompt", "$ ")]
    line.extend(_approval_command_prompt_parts(approval_summary(approval)))

    return [line]


def _approval_raw_commands(
    approval: dict[str, typing.Any]
) -> list[typing.Any]:
    """读取 shell_command 审批命令原文。"""
    if str(approval.get("tool") or "").strip() != "shell_command":
        fallback = approval.get("command", approval.get("resolved_command"))
        if isinstance(fallback, list):
            return [fallback]

        text = str(fallback or "").strip()
        return [text] if text else []

    return approval_shell_commands(approval)


def _approval_batch_command_lines(
    commands: list[typing.Any]
) -> list[list[tuple[str, str]]]:
    """按命令项原文生成批量命令审批树。"""
    lines: list[list[tuple[str, str]]] = [
        [("class:approval-prompt", "$ "), ("class:approval-command", f"{len(commands)} commands")]
    ]
    for index, command in enumerate(commands):
        lines.extend(_approval_command_item_lines(
            command, is_last=index == len(commands) - 1
        ))

    return lines


def _approval_single_command_lines(command: typing.Any) -> list[list[tuple[str, str]]]:
    """按命令原文生成单命令审批展示行。"""
    raw_lines = _approval_command_raw_lines(command)
    if not raw_lines:
        return [[("class:approval-prompt", "$ ")]]

    lines: list[list[tuple[str, str]]] = []

    first_line: list[tuple[str, str]] = [("class:approval-prompt", "$ ")]
    first_line.extend(_approval_command_prompt_parts(raw_lines[0]))

    lines.append(first_line)

    for raw_line in raw_lines[1:]:
        line: list[tuple[str, str]] = [("class:approval-prompt", "  ")]
        if raw_line:
            line.extend(_approval_command_prompt_parts(raw_line))
        lines.append(line)

    return lines


def _approval_command_item_lines(
    command: typing.Any,
    *,
    is_last: bool
) -> list[list[tuple[str, str]]]:
    """把单个命令项原文转换为树形展示行。"""
    connector = "└─ " if is_last else "├─ "
    prefix    = "   " if is_last else "│  "

    raw_lines = _approval_command_raw_lines(command)
    if not raw_lines:
        return [[("class:approval-prompt", connector)]]

    lines: list[list[tuple[str, str]]] = []

    first_line: list[tuple[str, str]] = [("class:approval-prompt", connector)]
    first_line.extend(_approval_command_prompt_parts(raw_lines[0]))

    lines.append(first_line)

    for raw_line in raw_lines[1:]:
        line: list[tuple[str, str]] = [("class:approval-prompt", prefix)]
        if raw_line:
            line.extend(_approval_command_prompt_parts(raw_line))
        lines.append(line)

    return lines


def _approval_command_raw_lines(command: typing.Any) -> list[str]:
    """返回命令原文行，数组命令保持参数间空格，多行参数保留原始换行。"""
    text = command_text(command) if isinstance(command, list) else str(command or "").strip()

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1]:
        lines.pop()

    return lines or [""]


def _approval_command_prompt_parts(command: str) -> list[tuple[str, str]]:
    """复用工具轨迹的命令着色规则。"""
    out: list[tuple[str, str]] = []

    for part in render_command_parts(command):
        text = str(part.get("text") or "")
        if not text:
            continue
        out.append((_approval_command_prompt_style(part.get("style")), text))

    return out


def _approval_command_prompt_style(style: str | None) -> str:
    """把工具轨迹命令样式映射为 prompt_toolkit class。"""
    if style == COMMAND_HEAD_STYLE:
        return "class:command-head"
    if style == COMMAND_FLAG_STYLE:
        return "class:command-flag"
    if style == COMMAND_PATH_STYLE:
        return "class:command-path"
    if style == COMMAND_STRING_STYLE:
        return "class:command-string"
    if style == COMMAND_NUMBER_STYLE:
        return "class:command-number"
    if style == COMMAND_OPERATOR_STYLE:
        return "class:command-operator"
    if style == COMMAND_STYLE:
        return "class:command"

    return ""


def _wrap_approval_menu_lines(
    lines: list[list[tuple[str, str]]],
    *,
    max_width: int,
    max_height: int | None
) -> list[list[tuple[str, str]]]:
    """按显示宽度换行，并按高度预算截断命令区。"""
    wrapped_by_line = [
        _wrap_fragment_line(line, max_width=max_width)
        for line in lines
    ]
    if max_height is None:
        return _flatten_wrapped_lines(wrapped_by_line)

    command_range = _approval_command_line_range(lines)
    if command_range is None:
        return _flatten_wrapped_lines(wrapped_by_line)

    start, end = command_range

    before  = _flatten_wrapped_lines(wrapped_by_line[:start])
    command = _flatten_wrapped_lines(wrapped_by_line[start:end])
    after   = _flatten_wrapped_lines(wrapped_by_line[end:])

    command_budget = _approval_command_line_budget(
        non_command_lines=len(before) + len(after),
        max_height=max_height
    )

    if len(command) <= command_budget:
        return [*before, *command, *after]

    keep    = max(0, command_budget - 1)
    omitted = len(command) - keep

    return [
        *before,
        *command[:keep],
        _approval_command_omitted_line(omitted),
        *after
    ]


def _flatten_wrapped_lines(
    groups: list[list[list[tuple[str, str]]]]
) -> list[list[tuple[str, str]]]:
    """展开按原始行分组的换行结果。"""
    return [line for group in groups for line in group]


def _approval_command_line_range(
    lines: list[list[tuple[str, str]]]
) -> tuple[int, int] | None:
    """返回审批菜单中命令区的原始行范围。"""
    start: int | None = None

    for index, line in enumerate(lines):
        if _is_approval_command_line(line):
            start = index
            break
    if start is None:
        return None

    end = start
    while end < len(lines) and _is_approval_command_line(lines[end]):
        end += 1

    return start, end


def _is_approval_command_line(line: list[tuple[str, str]]) -> bool:
    """判断语义行是否属于审批命令区。"""
    return bool(line and line[0][0] == "class:approval-prompt")


def _approval_command_line_budget(
    *,
    non_command_lines: int,
    max_height: int
) -> int:
    """按终端高度计算命令区最多可占用的显示行数。"""
    render_overhead = 4

    available = int(max_height or 0) - int(non_command_lines) - render_overhead

    return max(1, available)


def _approval_command_omitted_line(omitted: int) -> list[tuple[str, str]]:
    """生成命令区截断提示行。"""
    noun = "command line" if omitted == 1 else "command lines"
    return [("class:approval-preview", f"… +{omitted} {noun}")]


def _wrap_fragment_line(
    line: list[tuple[str, str]],
    *,
    max_width: int
) -> list[list[tuple[str, str]]]:
    """按显示宽度换单行片段。"""
    if max_width <= 0 or approval_menu_line_width(line) <= max_width:
        return [line]

    out: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]]   = []

    current_width = 0
    continuation  = _line_continuation_prefix(line)

    for style, text in line:
        for token in _wrap_tokens(text):
            token_width = get_cwidth(token)
            if current and current_width + token_width > max_width:
                out.append(current)

                current       = continuation.copy()
                current_width = approval_menu_line_width(current)
                if token.isspace():
                    continue

            if token_width > max_width:
                continuation_width = approval_menu_line_width(continuation)
                chunk_width        = max(1, max_width - continuation_width)

                for chunk in _split_wide_token(token, max_width=chunk_width):
                    if current:
                        out.append(current)

                        current       = continuation.copy()
                        current_width = continuation_width

                    current.append((style, chunk))
                    current_width += get_cwidth(chunk)

                continue

            current.append((style, token))
            current_width += token_width

    if current or not out:
        out.append(current)

    return out


def _line_continuation_prefix(line: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """根据行首树形前缀生成续行缩进。"""
    prefix: str = ""
    for _style, text in line:
        value = str(text or "")
        if not value or not value.strip():
            prefix += value
            continue
        for marker in ("├─ ", "└─ "):
            if value == marker:
                prefix += "│  " if marker == "├─ " else "   "
                continue
        if value in {"│  ", "   "}:
            prefix += value
            continue
        break

    return [("", prefix)] if prefix else _default_continuation_prefix()


def _default_continuation_prefix() -> list[tuple[str, str]]:
    """返回长行续行缩进。"""
    return [("", " " * APPROVAL_MENU_CONTINUATION_INDENT)]


def _wrap_tokens(text: str) -> list[str]:
    """把文本切成适合换行的 token，优先保留非空白片段完整。"""
    return re.findall(r"\S+\s*|\s+", text)


def _split_wide_token(token: str, *, max_width: int) -> list[str]:
    """把单个超宽 token 按显示宽度拆分。"""
    chunks: list[str] = []

    current: str       = ""
    current_width: int = 0

    for char in token:
        char_width = get_cwidth(char)
        if current and current_width + char_width > max_width:
            chunks.append(current)

            current       = ""
            current_width = 0

        current += char
        current_width += char_width

    if current:
        chunks.append(current)

    return chunks


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
