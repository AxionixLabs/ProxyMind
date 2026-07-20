# -*- coding: utf-8 -*-
# Notes: ==== Mind TUI ====

import typing

from prompt_toolkit.styles import Style

from mind_app.stream_events.approval_trace import (
    approval_shell_commands,
    approval_summary,
)
from mind_app.stream_events.command_preview import command_text
from mind_app.stream_events.tool_traces.command_parts import render_command_parts
from mind_app.stream_events.tool_traces.common import (
    COMMAND_FLAG_STYLE,
    COMMAND_HEAD_STYLE,
    COMMAND_NUMBER_STYLE,
    COMMAND_OPERATOR_STYLE,
    COMMAND_PATH_STYLE,
    COMMAND_STRING_STYLE,
    COMMAND_STYLE,
)

from .models import ApprovalDecisionValue
from .policy import (
    DECISION_SHORTCUT_LABELS,
    approval_decision_label,
    approval_expiry_label,
    approval_prompt,
)


APPROVAL_MENU_STYLE = Style.from_dict({
    "radio": "#8B96A3",
    "radio-selected": "bold #4DE3FF",
    "shortcut": "dim #6F7B88",
    "shortcut-selected": "bold #C7F7FF",
    "approval-pending": "bold #4DE3FF",
    "approval-prompt": "#7D8A98",
    "approval-preview": "dim #8896A5",
    "command": COMMAND_STYLE,
    "command-head": COMMAND_HEAD_STYLE,
    "command-flag": COMMAND_FLAG_STYLE,
    "command-path": COMMAND_PATH_STYLE,
    "command-string": COMMAND_STRING_STYLE,
    "command-number": COMMAND_NUMBER_STYLE,
    "command-operator": COMMAND_OPERATOR_STYLE,
})


def approval_menu_content_lines(
    decisions: list[ApprovalDecisionValue],
    *,
    approval: dict[str, typing.Any] | None,
    selected_index: int = 0,
) -> list[list[tuple[str, str]]]:
    """生成无边框审批卡的语义内容行。"""
    lines: list[list[tuple[str, str]]] = []
    if approval is not None:
        lines.extend(_approval_question_lines(approval))
        lines.extend(_approval_command_lines(approval))
        if expiry_label := approval_expiry_label(approval):
            lines.append([("class:approval-preview", expiry_label)])
        lines.append([])

    for index, decision in enumerate(decisions, start=1):
        active = index - 1 == selected_index
        label_style = "class:radio-selected" if active else "class:radio"
        shortcut_style = (
            "class:shortcut-selected" if active else "class:shortcut"
        )
        line: list[tuple[str, str]] = [
            ("class:radio", f"{'›' if active else ' '} {index}. ")
        ]
        line.extend(_decision_display_prompt_parts(
            decision,
            approval=approval,
            style=label_style,
            shortcut_style=shortcut_style,
        ))
        lines.append(line)
    return lines


def _approval_question_lines(
    approval: dict[str, typing.Any],
) -> list[list[tuple[str, str]]]:
    """生成审批询问文案。"""
    return [[("class:approval-pending", approval_prompt(approval))], []]


def _approval_command_lines(
    approval: dict[str, typing.Any],
) -> list[list[tuple[str, str]]]:
    """生成审批命令区域。"""
    commands = _approval_raw_commands(approval)
    if commands:
        return _approval_single_command_lines(commands[0])
    line: list[tuple[str, str]] = [("class:approval-prompt", "$ ")]
    line.extend(_approval_command_prompt_parts(approval_summary(approval)))
    return [line]


def _approval_raw_commands(approval: dict[str, typing.Any]) -> list[typing.Any]:
    """读取审批请求中的命令原文。"""
    if str(approval.get("tool") or "").strip() not in {
        "shell_command",
        "exec_command",
    }:
        fallback = approval.get("command", approval.get("resolved_command"))
        if isinstance(fallback, list):
            return [fallback]
        text = str(fallback or "").strip()
        return [text] if text else []
    return approval_shell_commands(approval)


def _approval_single_command_lines(
    command: typing.Any,
) -> list[list[tuple[str, str]]]:
    """按命令原文生成审批展示行。"""
    raw_lines = _approval_command_raw_lines(command)
    first_line: list[tuple[str, str]] = [("class:approval-prompt", "$ ")]
    first_line.extend(_approval_command_prompt_parts(raw_lines[0]))
    lines = [first_line]
    for raw_line in raw_lines[1:]:
        line: list[tuple[str, str]] = [("class:approval-prompt", "  ")]
        if raw_line:
            line.extend(_approval_command_prompt_parts(raw_line))
        lines.append(line)
    return lines


def _approval_command_raw_lines(command: typing.Any) -> list[str]:
    """将一项审批命令转换为原始文本行。"""
    text = command_text(command) if isinstance(command, list) else str(command or "").strip()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1]:
        lines.pop()
    return lines or [""]


def _approval_command_prompt_parts(command: str) -> list[tuple[str, str]]:
    """使用工具轨迹的命令着色规则生成文本片段。"""
    out: list[tuple[str, str]] = []
    for part in render_command_parts(command):
        text = str(part.get("text") or "")
        if text:
            out.append((_approval_command_prompt_style(part.get("style")), text))
    return out


def _approval_command_prompt_style(style: str | None) -> str:
    """将工具轨迹命令样式映射为 TUI 样式类。"""
    styles = {
        COMMAND_HEAD_STYLE: "class:command-head",
        COMMAND_FLAG_STYLE: "class:command-flag",
        COMMAND_PATH_STYLE: "class:command-path",
        COMMAND_STRING_STYLE: "class:command-string",
        COMMAND_NUMBER_STYLE: "class:command-number",
        COMMAND_OPERATOR_STYLE: "class:command-operator",
        COMMAND_STYLE: "class:command",
    }
    return styles.get(style, "")


def _decision_display_prompt_parts(
    decision: str,
    *,
    approval: dict[str, typing.Any] | None,
    style: str,
    shortcut_style: str,
) -> list[tuple[str, str]]:
    """返回审批选项的分段展示文案。"""
    label    = approval_decision_label(approval, decision)
    shortcut = DECISION_SHORTCUT_LABELS.get(decision, "")
    parts    = [(style, label)]

    if shortcut:
        parts.extend([(style, " ("), (shortcut_style, shortcut), (style, ")")])
    return parts


if __name__ == '__main__':
    pass
