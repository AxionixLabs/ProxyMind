# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.styles import Style
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.approval.policy import (
    DECISION_SHORTCUT_LABELS,
    approval_decision_label,
    approval_expiry_label,
    approval_prompt
)
from mind_app.stream_events.approval_trace import (
    approval_shell_commands,
    approval_summary
)
from mind_app.stream_events.command_preview import command_text
from mind_app.stream_events.tool_traces.command_parts import render_command_parts
from mind_app.presentation.styles import (
    COMMAND_FLAG_STYLE,
    COMMAND_HEAD_STYLE,
    COMMAND_NUMBER_STYLE,
    COMMAND_OPERATOR_STYLE,
    COMMAND_PATH_STYLE,
    COMMAND_STRING_STYLE
)

TUI_APPROVAL_STYLE = Style.from_dict({
    "approval-card"              : "bg:#E2E5E8 #252A30",
    "approval-title"             : "bg:#E2E5E8 bold #20262D",
    "approval-context"           : "bg:#E2E5E8 #48515B",
    "approval-meta"              : "bg:#E2E5E8 #626C77",
    "approval-option"            : "bg:#E2E5E8 #424B55",
    "approval-option-selected"   : "bg:#E2E5E8 bold #005F73",
    "approval-shortcut"          : "bg:#E2E5E8 #68727D",
    "approval-shortcut-selected" : "bg:#E2E5E8 bold #006D77",
    "approval-command"           : "bg:#E2E5E8 #252A30",
    "approval-command-head"      : "bg:#E2E5E8 bold #164E63",
    "approval-command-flag"      : "bg:#E2E5E8 #7C4A03",
    "approval-command-path"      : "bg:#E2E5E8 #374151",
    "approval-command-string"    : "bg:#E2E5E8 #166534",
    "approval-command-number"    : "bg:#E2E5E8 #9A3412",
    "approval-command-operator"  : "bg:#E2E5E8 #6D28D9",
})


def tui_approval_content_lines(
    decisions: list[ApprovalDecisionValue],
    *,
    approval: dict[str, typing.Any],
    selected_index: int = 0,
) -> list[list[tuple[str, str]]]:
    """生成 TUI 专属审批面板的内容行。"""
    lines: list[list[tuple[str, str]]] = [
        [("class:approval-title", approval_prompt(approval))],
    ]
    lines.extend(_approval_command_lines(approval))
    if expiry_label := approval_expiry_label(approval):
        lines.append([("class:approval-meta", expiry_label)])

    for index, decision in enumerate(decisions, start=1):
        active = index - 1 == selected_index
        label_style = (
            "class:approval-option-selected"
            if active
            else "class:approval-option"
        )
        shortcut_style = (
            "class:approval-shortcut-selected"
            if active
            else "class:approval-shortcut"
        )
        line: list[tuple[str, str]] = [
            (label_style, f"{'>' if active else ' '} {index}. "),
        ]
        line.extend(_decision_parts(
            decision,
            approval=approval,
            label_style=label_style,
            shortcut_style=shortcut_style,
        ))
        lines.append(line)
    return lines


def _approval_command_lines(
    approval: dict[str, typing.Any],
) -> list[list[tuple[str, str]]]:
    """生成审批命令区域。"""
    commands = _approval_raw_commands(approval)
    if commands:
        return _single_command_lines(commands[0])
    line: list[tuple[str, str]] = [
        ("class:approval-context", "$ "),
    ]
    line.extend(_command_parts(approval_summary(approval)))
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


def _single_command_lines(command: typing.Any) -> list[list[tuple[str, str]]]:
    """把单项命令转换为多行审批预览。"""
    raw_lines = _command_raw_lines(command)

    lines: list[list[tuple[str, str]]] = []
    for index, raw_line in enumerate(raw_lines):
        line: list[tuple[str, str]] = [
            ("class:approval-context", "$ " if index == 0 else "  "),
        ]
        line.extend(_command_parts(raw_line))
        lines.append(line)
    return lines


def _command_raw_lines(command: typing.Any) -> list[str]:
    """保留命令预览中的原始换行。"""
    text = (
        command_text(command)
        if isinstance(command, list)
        else str(command or "").strip()
    )
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1]:
        lines.pop()
    return lines or [""]


def _command_parts(command: str) -> list[tuple[str, str]]:
    """把命令片段映射为浅色审批面板样式。"""
    styles = {
        COMMAND_HEAD_STYLE     : "class:approval-command-head",
        COMMAND_FLAG_STYLE     : "class:approval-command-flag",
        COMMAND_PATH_STYLE     : "class:approval-command-path",
        COMMAND_STRING_STYLE   : "class:approval-command-string",
        COMMAND_NUMBER_STYLE   : "class:approval-command-number",
        COMMAND_OPERATOR_STYLE : "class:approval-command-operator",
    }
    return [
        (styles.get(part.style, "class:approval-command"), part.text)
        for part in render_command_parts(command)
        if part.text
    ]


def _decision_parts(
    decision: str,
    *,
    approval: dict[str, typing.Any],
    label_style: str,
    shortcut_style: str,
) -> list[tuple[str, str]]:
    """生成审批选项标签和快捷键片段。"""
    label    = approval_decision_label(approval, decision)
    shortcut = DECISION_SHORTCUT_LABELS.get(decision, "")
    parts    = [(label_style, label)]

    if shortcut:
        parts.extend([
            (label_style, "  "),
            (shortcut_style, shortcut),
        ])
    return parts


if __name__ == '__main__':
    pass
