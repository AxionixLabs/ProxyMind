# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import unicodedata
from metadata import const
from mind_app.approval.policy import approval_execpolicy_amendment
from mind_app.presentation.models import (
    ApprovalSource,
    TextSpan,
    TextStyle
)
from .command_preview import command_preview
from .tool_trace import (
    TITLE_STYLE,
    ERROR_STYLE
)

APPROVAL_APPROVED_STYLE = TextStyle(foreground="#6EE7A8", bold=True)
APPROVAL_DENIED_STYLE   = ERROR_STYLE
APPROVAL_COMMAND_STYLE  = TITLE_STYLE
APPROVAL_TOOL_STYLE     = TextStyle(foreground="#7DD3FC", bold=True)
APPROVAL_ARG_STYLE      = TextStyle(foreground="#A7F3D0", bold=True)
APPROVAL_RES_STYLE      = TextStyle(foreground="#8FA4B8", dim=True)
APPROVAL_SCOPE_STYLE    = TextStyle(foreground="#A7F3D0", bold=True)

APPROVAL_SNIPPET_MAX_GRAPHEMES = 80


def approval_summary(approval: dict[str, typing.Any]) -> str:
    """生成审批请求的简短摘要。"""
    command = _approval_command_summary(approval)
    if not command:
        command = command_preview(approval.get("command")).title
    tool = str(approval.get("tool") or "").strip()
    return _short_approval_summary(command or tool or "tool call")


def _short_approval_summary(value: typing.Any) -> str:
    """截断审批提示里的单行命令摘要。"""
    return _truncate_approval_snippet(value)


def _truncate_approval_snippet(value: typing.Any) -> str:
    """按审批历史规则生成单行命令摘要。"""
    text = str(value or "").strip()
    if not text:
        return ""

    lines = text.splitlines()
    if len(lines) > 1:
        text = f"{lines[0]} ..."

    units = list(_approval_graphemes(text))
    if len(units) <= APPROVAL_SNIPPET_MAX_GRAPHEMES:
        return text
    return "".join(units[:APPROVAL_SNIPPET_MAX_GRAPHEMES - 3]) + "..."


def _approval_graphemes(text: str) -> typing.Iterator[str]:
    """迭代审批摘要中不应被截断的 Unicode 文本单元。"""
    value = str(text or "")
    start = 0
    while start < len(value):
        end = _approval_grapheme_end(value, start)
        yield value[start:end]
        start = end


def _approval_grapheme_end(text: str, start: int) -> int:
    """返回一个审批摘要文本单元的结束位置。"""
    limit = len(text)
    index = min(limit, max(0, int(start)))
    if index >= limit:
        return limit

    first = text[index]
    index += 1
    if _approval_regional_indicator(first):
        if index < limit and _approval_regional_indicator(text[index]):
            index += 1
        return index

    while index < limit:
        char = text[index]
        if _approval_extends_grapheme(char):
            index += 1
            continue
        if char == "\u200d" and index + 1 < limit:
            index += 2
            continue
        break
    return index


def _approval_extends_grapheme(char: str) -> bool:
    """判断字符是否延续前一个审批摘要文本单元。"""
    codepoint = ord(char)
    return bool(
        unicodedata.combining(char)
        or unicodedata.category(char).startswith("M")
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _approval_regional_indicator(char: str) -> bool:
    """判断字符是否为区域指示符。"""
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _approval_arguments(approval: dict[str, typing.Any]) -> dict[str, typing.Any]:
    raw = approval.get("arguments", approval.get("args"))
    return dict(raw) if isinstance(raw, dict) else {}


def approval_shell_commands(approval: dict[str, typing.Any]) -> list[typing.Any]:
    """从审批参数里提取单条 shell 命令，兼容预览字段。"""
    arguments        = _approval_arguments(approval)
    argument_command = arguments.get("command")

    if isinstance(argument_command, list):
        return [argument_command]

    text = str(argument_command or "").strip()
    if text:
        return [text]

    command = approval.get("command", approval.get("resolved_command"))
    if isinstance(command, list):
        return [command]

    text = str(command or "").strip()
    if text:
        return [text]

    return []


def _approval_command_summary(approval: dict[str, typing.Any]) -> str:
    """生成 shell 命令审批摘要。"""
    commands = approval_shell_commands(approval)
    if not commands:
        return ""
    return command_preview(commands[0]).title or commands[0]


def _approval_amendment_snippet(approval: dict[str, typing.Any]) -> str:
    """生成命令前缀策略批准后的单行前缀摘要。"""
    amendment = approval_execpolicy_amendment(approval)
    if amendment is None:
        return ""

    return _truncate_approval_snippet(amendment.display)


def render_approval_approved_trace(
    approval: dict[str, typing.Any],
    *,
    decision: str = "accept",
    source: ApprovalSource = "user"
) -> str:
    """生成审批通过后的轨迹标题。"""
    summary = approval_summary(approval)
    if source == "hook":
        return f"✔ Hook approved {summary}".rstrip()
    if source == "policy":
        return f"✔ Approval policy approved {summary}".rstrip()
    if source == "auto_review":
        rationale = _review_rationale(approval)
        suffix = f" · {rationale}" if rationale else ""
        return f"✔ Auto review approved {summary}{suffix}".rstrip()

    if decision == "acceptWithExecpolicyAmendment":
        amendment = _approval_amendment_snippet(approval)
        if amendment:
            return (
                f"✔ You approved {const.APP_NAME} to always run commands that "
                f"start with {amendment}"
            ).rstrip()
        scope = "with the proposed command policy"
    elif decision == "acceptForSession":
        scope = "every time this session"
    else:
        scope = "this time"
    return f"✔ You approved {const.APP_NAME} to run {summary} {scope}".rstrip()


def render_approval_denied_trace(
    approval: dict[str, typing.Any],
    *,
    source: ApprovalSource = "user"
) -> str:
    """生成审批拒绝后的轨迹标题。"""
    summary = approval_summary(approval)
    if source == "hook":
        return f"• Hook denied {summary}".rstrip()
    if source == "policy":
        return f"• Approval policy denied {summary}".rstrip()
    if source == "auto_review":
        rationale = _review_rationale(approval)
        suffix = f" · {rationale}" if rationale else ""
        return f"• Auto review denied {summary}{suffix}".rstrip()

    return f"• You denied {const.APP_NAME} to run {summary}".rstrip()


def _review_rationale(approval: dict[str, typing.Any]) -> str:
    """读取自动审批解释并压缩为单行展示文本。"""
    return " ".join(str(
        approval.get("rationale") or approval.get("failure_reason") or ""
    ).split())


def render_approval_cancelled_trace(approval: dict[str, typing.Any]) -> str:
    """生成审批取消后的轨迹标题。"""
    summary = approval_summary(approval)
    return f"• You cancelled {summary} · turn was interrupted".rstrip()


def render_approval_trace_parts(
    title: str,
    *,
    approval: dict[str, typing.Any] | None = None,
    state: typing.Literal["approved", "denied"] = "approved"
) -> list[TextSpan]:
    """生成审批轨迹的分段样式内容。"""
    if state == "denied":
        title_style = APPROVAL_DENIED_STYLE
    else:
        title_style = APPROVAL_APPROVED_STYLE

    return _approval_title_parts(title, approval or {}, base_style=title_style)


def _approval_title_parts(
    title: str,
    approval: dict[str, typing.Any],
    *,
    base_style: TextStyle
) -> list[TextSpan]:
    """把审批 trace 标题拆成状态文本、工具名和参数。"""
    amendment = _approval_amendment_snippet(approval)
    if (
        base_style == APPROVAL_APPROVED_STYLE
        and amendment
        and "always run commands that start with" in title
        and amendment in title
    ):
        start = title.find(amendment)
        end = start + len(amendment)
        parts: list[TextSpan] = []
        if start:
            parts.append(TextSpan(title[:start], base_style))
        parts.append(TextSpan(amendment, APPROVAL_RES_STYLE))
        if end < len(title):
            parts.append(TextSpan(title[end:], base_style))
        return parts

    summary = approval_summary(approval)
    if not summary or summary not in title:
        return [TextSpan(title, base_style)]

    start = title.find(summary)
    end   = start + len(summary)

    parts: list[TextSpan] = []

    if start:
        parts.append(TextSpan(title[:start], base_style))
    parts.extend(_approval_summary_parts(summary, approval, base_style=base_style))
    if end < len(title):
        parts.extend(_approval_suffix_parts(title[end:], base_style=base_style))
    return parts


def _approval_suffix_parts(
    suffix: str,
    *,
    base_style: TextStyle
) -> list[TextSpan]:
    """把审批通过后的作用域提示单独着色。"""
    if base_style != APPROVAL_APPROVED_STYLE:
        return [TextSpan(suffix, base_style)]

    for scope in ("every time this session", "for this session", "this time"):
        if suffix.endswith(scope):
            prefix = suffix[:-len(scope)]
            parts: list[TextSpan] = []
            if prefix:
                parts.append(TextSpan(prefix, base_style))
            parts.append(TextSpan(scope, APPROVAL_SCOPE_STYLE))
            return parts

    return [TextSpan(suffix, base_style)]


def _approval_summary_parts(
    summary: str,
    approval: dict[str, typing.Any],
    *,
    base_style: TextStyle
) -> list[TextSpan]:
    """把审批摘要拆成工具名和参数片段。"""
    if base_style == APPROVAL_APPROVED_STYLE:
        return [TextSpan(summary, APPROVAL_RES_STYLE)]

    tool = str(approval.get("tool") or "").strip()
    if tool and summary.startswith(tool):
        rest = summary[len(tool):]
        parts: list[TextSpan] = [
            TextSpan(tool, APPROVAL_TOOL_STYLE)
        ]
        if rest:
            parts.append(TextSpan(rest, APPROVAL_ARG_STYLE))
        return parts

    return [TextSpan(summary, APPROVAL_COMMAND_STYLE)]


if __name__ == '__main__':
    pass
