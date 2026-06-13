# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import shutil
import re
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
from prompt_toolkit.utils import get_cwidth
from mind_app.stream_events.approval_trace import (
    APPROVAL_ARG_STYLE,
    APPROVAL_COMMAND_STYLE,
    APPROVAL_PENDING_STYLE,
    APPROVAL_PROMPT_STYLE,
    APPROVAL_TOOL_STYLE,
    approval_command_preview,
    approval_summary
)
from mind_app.stream_events.command_preview import command_preview
from mind_app.stream_events.tool_trace import (
    PREVIEW_STYLE,
    render_tool_trace_parts
)
from mind_nova import const

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
    "decline"          : f"No, and tell {const.APP_DESC} what to do differently",
    "cancel"           : "Cancel"
}

DECISION_SHORTCUT_LABELS: dict[str, str] = {
    "accept"           : "y",
    "acceptForSession" : "",
    "decline"          : "esc",
    "cancel"           : "esc"
}

APPROVAL_MENU_STYLE = Style.from_dict({
    "radio-list"        : "",
    "radio"             : "bold #9AA9B5",
    "radio-selected"    : "bold #A7C7FF",
    "radio-checked"     : "bold #E2E8F0",
    "radio-number"      : "bold #9AA9B5",
    "shortcut"          : "dim #8FA4B8",
    "shortcut-selected" : "bold #E2E8F0",
    "approval-pending"  : "bold #D7E7FF",
    "approval-prompt"   : "bold #8FA4B8",
    "approval-tool"     : "bold #7DD3FC",
    "approval-arg"      : "#AFC7D8",
    "approval-command"  : "bold #E2E8F0",
    "approval-border"   : "#6F8498",
    "approval-preview"  : "dim #A5B3C2"
})

APPROVAL_SHORTCUT_STYLE = "dim #8FA4B8"

APPROVAL_MENU_PADDING             = 3
APPROVAL_MENU_MIN_INNER_WIDTH     = 36
APPROVAL_MENU_TERMINAL_MARGIN     = 4
APPROVAL_MENU_TITLE_MIN_RULE      = 4
APPROVAL_MENU_CONTINUATION_INDENT = 2


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


def _approval_arguments(
    approval: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """读取并规范化审批请求参数。"""
    raw = approval.get("arguments", approval.get("args"))
    arguments = dict(raw) if isinstance(raw, dict) else {}
    return _canonical_tool_arguments(arguments)


def _canonical_tool_arguments(
    arguments: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """返回可稳定比较的工具参数。"""
    return typing.cast(dict[str, typing.Any], _normalize_value(arguments))


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
    noun    = _approval_prompt_noun(approval)

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
            *_decision_display_parts(
                decision,
                label_style="bold #E2E8F0" if index == 1 else "bold #9AA9B5"
            )
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
        lines.append(f"{prefix} {index}. {_decision_display_label(decision)}")
    return "\n".join(lines) + "\n"


def approval_prompt_parts(
    approval: dict[str, typing.Any]
) -> list[dict[str, str | None]]:
    """构造审批提示的文本片段。"""
    noun = _approval_prompt_noun(approval)
    parts: list[dict[str, str | None]] = [
        {"text": f"Would you like to approve the following {noun}?\n\n", "style": APPROVAL_PENDING_STYLE},
        *approval_command_parts(approval, newline=True)
    ]
    preview = approval_command_preview(approval)
    if preview.full:
        parts.extend([
            *render_tool_trace_parts("", preview=preview)
        ])
    return parts


def approval_command_parts(
    approval: dict[str, typing.Any],
    *,
    newline: bool
) -> list[dict[str, str | None]]:
    """把审批命令行拆成 `$`、工具名和参数片段。"""
    summary = approval_summary(approval)
    tool    = str(approval.get("tool") or "").strip()

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
    """返回审批提示中使用的操作类型名称。"""
    tool = str(approval.get("tool") or "").strip()
    return "command" if tool in {"", "shell_command"} else "tool action"


def _approval_choice_renderable(approval: dict[str, typing.Any]) -> Text:
    """构造带间距的审批选项渲染对象。"""
    out = Text()
    out.append("\n")
    for part in approval_choice_parts(approval):
        out.append(str(part.get("text") or ""), style=part.get("style"))
    return out


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


def _decision_display_label(decision: str) -> str:
    """返回审批选项的展示文案，包含可用快捷键提示。"""
    label    = DECISION_LABELS.get(decision, decision)
    shortcut = DECISION_SHORTCUT_LABELS.get(decision, "")
    return f"{label} ({shortcut})" if shortcut else label


def _decision_display_parts(
    decision: str,
    *,
    label_style: str
) -> list[dict[str, str | None]]:
    """返回审批选项的分段展示文案。"""
    label    = DECISION_LABELS.get(decision, decision)
    shortcut = DECISION_SHORTCUT_LABELS.get(decision, "")
    parts: list[dict[str, str | None]] = [
        {"text": label, "style": label_style}
    ]
    if shortcut:
        parts.extend([
            {"text": " (", "style": label_style},
            {"text": shortcut, "style": APPROVAL_SHORTCUT_STYLE},
            {"text": ")", "style": label_style},
        ])
    return parts


def _decision_display_prompt_parts(
    decision: str,
    *,
    style: str,
    shortcut_style: str
) -> list[tuple[str, str]]:
    """返回 prompt_toolkit 菜单选项的分段展示文案。"""
    label    = DECISION_LABELS.get(decision, decision)
    shortcut = DECISION_SHORTCUT_LABELS.get(decision, "")
    parts    = [(style, label)]

    if shortcut:
        parts.extend(
            [(style, " ("), (shortcut_style, shortcut), (style, ")")]
        )

    return parts


def _approval_promptkit_style(style: str | None) -> str:
    """把 Rich 审批样式映射为 prompt_toolkit class。"""
    if style == APPROVAL_PENDING_STYLE:
        return "class:approval-pending"
    if style == APPROVAL_PROMPT_STYLE:
        return "class:approval-prompt"
    if style == APPROVAL_TOOL_STYLE:
        return "class:approval-tool"
    if style == APPROVAL_ARG_STYLE:
        return "class:approval-arg"
    if style == APPROVAL_COMMAND_STYLE:
        return "class:approval-command"
    if style == PREVIEW_STYLE:
        return "class:approval-preview"

    return ""


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
        lines.extend(_approval_preview_menu_lines(approval))
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
                decision, style=label_style, shortcut_style=shortcut_style
            )
        )
        lines.append(line)

    return lines


def render_bordered_approval_menu(
    lines: list[list[tuple[str, str]]],
    *,
    max_width: int | None = None,
    padding: int = APPROVAL_MENU_PADDING,
    title: str | None = "Approval required"
) -> list[tuple[str, str]]:
    """把审批菜单内容行渲染为带边框的 prompt_toolkit 片段。"""
    content_width = approval_menu_content_width(lines, max_width=max_width, padding=padding)
    wrapped_lines = _wrap_fragment_lines(lines, max_width=content_width)
    horizontal_width = content_width + padding * 2
    parts: list[tuple[str, str]] = [
        ("", "\n"),
        *_approval_top_border_parts(horizontal_width, title=title),
        ("class:approval-border", "\n")
    ]

    for line in wrapped_lines:
        line_width = approval_menu_line_width(line)
        parts.append(("class:approval-border", "│"))
        parts.append(("", " " * padding))
        parts.extend(line)
        parts.append(("", " " * max(0, content_width - line_width)))
        parts.append(("", " " * padding))
        parts.append(("class:approval-border", "│\n"))

    parts.append(("class:approval-border", f"╰{'─' * horizontal_width}╯"))
    parts.append(("", "\n"))
    return parts


def approval_menu_content_width(
    lines: list[list[tuple[str, str]]],
    *,
    max_width: int | None = None,
    padding: int = APPROVAL_MENU_PADDING
) -> int:
    """计算审批卡内容区宽度，受终端最大宽度约束。"""
    natural_width = max((approval_menu_line_width(line) for line in lines), default=0)
    bounded_width = natural_width

    if max_width is not None:
        available = max(
            APPROVAL_MENU_MIN_INNER_WIDTH,
            int(max_width) - APPROVAL_MENU_TERMINAL_MARGIN - 2 - padding * 2
        )
        bounded_width = min(natural_width, available)

    return max(APPROVAL_MENU_MIN_INNER_WIDTH, bounded_width)


def approval_menu_line_width(line: list[tuple[str, str]]) -> int:
    """返回 prompt_toolkit 文本片段的终端显示宽度。"""
    return sum(get_cwidth(text) for _style, text in line)


def approval_menu_plain_text(parts: list[tuple[str, str]]) -> str:
    """把 prompt_toolkit 片段转为纯文本，供测试断言。"""
    return "".join(text for _style, text in parts)


def _approval_top_border_parts(
    width: int,
    *,
    title: str | None
) -> list[tuple[str, str]]:
    """生成包含居中标题的审批卡上边框。"""
    title_text = str(title or "").strip()
    if not title_text:
        return [("class:approval-border", f"╭{'─' * width}╮")]

    decorated = f" {title_text} "
    title_width = get_cwidth(decorated)
    min_width = title_width + APPROVAL_MENU_TITLE_MIN_RULE * 2
    if min_width > width:
        width = min_width

    if title_width >= width:
        return [("class:approval-border", f"╭{decorated}╮")]

    left = (width - title_width) // 2
    right = width - title_width - left
    return [
        ("class:approval-border", f"╭{'─' * left}"),
        ("class:approval-pending", decorated),
        ("class:approval-border", f"{'─' * right}╮")
    ]


def _approval_question_lines(
    approval: dict[str, typing.Any]
) -> list[list[tuple[str, str]]]:
    """生成审批询问文案。"""
    noun = _approval_prompt_noun(approval)
    return [
        [("class:approval-pending", f"Would you like to approve the following {noun}?")],
        []
    ]


def _approval_command_lines(
    approval: dict[str, typing.Any]
) -> list[list[tuple[str, str]]]:
    """生成审批命令区域。"""
    summary = command_preview(approval.get("command")).title or approval_summary(approval)
    tool    = str(approval.get("tool") or "").strip()
    line: list[tuple[str, str]] = [("class:approval-prompt", "$ ")]

    if tool and summary.startswith(tool):
        line.append(("class:approval-tool", tool))
        rest = summary[len(tool):]
        if rest:
            line.append(("class:approval-arg", rest))
    else:
        line.append(("class:approval-command", summary))

    return [line]


def _approval_preview_menu_lines(
    approval: dict[str, typing.Any]
) -> list[list[tuple[str, str]]]:
    """生成内联脚本预览行。"""
    preview = approval_command_preview(approval)
    if not preview.screen:
        return []

    return [
        [("class:approval-preview", f"└ {line}" if index == 0 else f"  {line}")]
        for index, line in enumerate(preview.screen.splitlines())
    ]


def _wrap_fragment_lines(
    lines: list[list[tuple[str, str]]],
    *,
    max_width: int
) -> list[list[tuple[str, str]]]:
    """按显示宽度换行，保留片段样式。"""
    wrapped: list[list[tuple[str, str]]] = []
    for line in lines:
        wrapped.extend(_wrap_fragment_line(line, max_width=max_width))
    return wrapped


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

    for style, text in line:
        for token in _wrap_tokens(text):
            token_width = get_cwidth(token)
            if token.isspace() and not current:
                continue
            if current and current_width + token_width > max_width:
                out.append(current)
                current = _continuation_prefix()
                current_width = APPROVAL_MENU_CONTINUATION_INDENT
                if token.isspace():
                    continue
            if token_width > max_width:
                chunk_width = max(1, max_width - APPROVAL_MENU_CONTINUATION_INDENT)
                for chunk in _split_wide_token(token, max_width=chunk_width):
                    if current:
                        out.append(current)
                        current = _continuation_prefix()
                        current_width = APPROVAL_MENU_CONTINUATION_INDENT
                    current.append((style, chunk))
                    current_width += get_cwidth(chunk)
                continue
            current.append((style, token))
            current_width += token_width

    if current or not out:
        out.append(current)

    return out


def _continuation_prefix() -> list[tuple[str, str]]:
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
            current = ""
            current_width = 0
        current += char
        current_width += char_width

    if current:
        chunks.append(current)

    return chunks


def _terminal_menu_width() -> int:
    """返回审批菜单可用的终端宽度。"""
    return max(40, shutil.get_terminal_size(fallback=(100, 24)).columns)


def _answer_to_decision(
    answer: typing.Any,
    decisions: list[ApprovalDecisionValue]
) -> ApprovalDecisionValue:
    """将用户输入转换为最终审批选择。"""
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
            title="Approval required" if approval is not None else None
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
        """取消审批并返回拒绝结果。"""
        event.app.exit(result="decline")

    @bindings.add("y")
    def _(event) -> None:
        """通过快捷键接受审批请求。"""
        if "accept" in decisions:
            event.app.exit(result="accept")

    @bindings.add("n")
    def _(event) -> None:
        """通过快捷键拒绝审批请求。"""
        event.app.exit(result="decline" if "decline" in decisions else decisions[-1])

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
    return str(await app.run_async())


if __name__ == '__main__':
    pass
