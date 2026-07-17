# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
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
from .models import ApprovalDecisionValue
from .policy import (
    DECISION_SHORTCUT_LABELS,
    approval_decision_label,
    approval_expiry_label,
    approval_prompt
)

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

_APPROVAL_MENU_PADDING             = COMPACT_RULE_PADDING
_APPROVAL_MENU_TERMINAL_MARGIN     = COMPACT_RULE_TERMINAL_MARGIN
_APPROVAL_MENU_FIXED_WIDTH         = 104
_APPROVAL_MENU_MAX_COMMAND_WIDTH   = _APPROVAL_MENU_FIXED_WIDTH - _APPROVAL_MENU_PADDING
_APPROVAL_MENU_CONTINUATION_INDENT = 2
_APPROVAL_MENU_TITLE_MIN_RULE      = 4


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
    padding: int = _APPROVAL_MENU_PADDING,
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


def approval_title(approval: dict[str, typing.Any] | None) -> str:
    """读取审批卡标题。"""
    if not isinstance(approval, dict):
        return "Review command"
    return str(approval.get("title") or "Review command").strip() or "Review command"


def approval_menu_plain_text(parts: list[tuple[str, str]]) -> str:
    """把 prompt_toolkit 片段转为纯文本。"""
    return "".join(text for _style, text in parts)


def _approval_rule_width(*, max_width: int | None) -> int:
    """按固定宽度策略计算审批卡规则线宽度。"""
    if max_width is None:
        return _APPROVAL_MENU_FIXED_WIDTH

    available = max(1, int(max_width) - _APPROVAL_MENU_TERMINAL_MARGIN)
    return min(_APPROVAL_MENU_FIXED_WIDTH, available)


def _approval_menu_line_width(line: list[tuple[str, str]]) -> int:
    """返回 prompt_toolkit 文本片段的终端显示宽度。"""
    return sum(get_cwidth(text) for _style, text in line)


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
    max_title_width = max(1, width - _APPROVAL_MENU_TITLE_MIN_RULE * 2)

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
    if commands:
        return _approval_single_command_lines(commands[0])

    line: list[tuple[str, str]] = [("class:approval-prompt", "$ ")]
    line.extend(_approval_command_prompt_parts(approval_summary(approval)))

    return [line]


def _approval_raw_commands(
    approval: dict[str, typing.Any]
) -> list[typing.Any]:
    """读取 shell 审批命令原文。"""
    if str(approval.get("tool") or "").strip() not in {"shell_command", "exec_command"}:
        fallback = approval.get("command", approval.get("resolved_command"))
        if isinstance(fallback, list):
            return [fallback]

        text = str(fallback or "").strip()
        return [text] if text else []

    return approval_shell_commands(approval)


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


def _approval_command_omitted_line(omitted: int) -> list[tuple[str, str]]:
    """生成命令区截断提示行。"""
    noun = "command line" if omitted == 1 else "command lines"
    return [("class:approval-preview", f"… +{omitted} {noun}")]


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


def _approval_command_line_budget(
    *,
    non_command_lines: int,
    max_height: int
) -> int:
    """按终端高度计算命令区最多可占用的显示行数。"""
    render_overhead = 4

    available = int(max_height or 0) - int(non_command_lines) - render_overhead

    return max(1, available)


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
    after   = _flatten_wrapped_lines(wrapped_by_line[end:])

    command = _flatten_wrapped_lines(wrapped_by_line[start:end])

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


def _is_approval_command_line(line: list[tuple[str, str]]) -> bool:
    """判断语义行是否属于审批命令区。"""
    return bool(line and line[0][0] == "class:approval-prompt")


def _wrap_fragment_line(
    line: list[tuple[str, str]],
    *,
    max_width: int
) -> list[list[tuple[str, str]]]:
    """按显示宽度换单行片段。"""
    if max_width <= 0 or _approval_menu_line_width(line) <= max_width:
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
                current_width = _approval_menu_line_width(current)
                if token.isspace():
                    continue

            if token_width > max_width:
                continuation_width = _approval_menu_line_width(continuation)
                chunk_width        = max(1, max_width - continuation_width)

                for chunk in _split_wide_token(token, max_width=chunk_width):
                    if current and current_width > continuation_width:
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
    return [("", " " * _APPROVAL_MENU_CONTINUATION_INDENT)]


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


if __name__ == '__main__':
    pass
