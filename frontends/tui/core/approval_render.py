# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from frontends.terminal.capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities
)
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.approval.presentation import (
    ApplyPatchApprovalPresentation,
    ApprovalCommand,
    ApprovalPresentation,
    ExecApprovalPresentation,
    RequestPermissionsApprovalPresentation,
    ToolApprovalPresentation,
    ensure_approval_presentation,
)
from mind_app.approval.policy import (
    DECISION_SHORTCUT_LABELS,
    approval_decision_label,
)
from mind_app.presentation.stream.tool_traces.command_parts import render_command_parts
from agent.ports.presentation import StyledBlock
from mind_app.presentation.renderers.patch import render_patch_view
from mind_app.presentation.styles import (
    COMMAND_STYLE,
    COMMAND_FLAG_STYLE,
    COMMAND_HEAD_STYLE,
    COMMAND_NUMBER_STYLE,
    COMMAND_OPERATOR_STYLE,
    COMMAND_PATH_STYLE,
    COMMAND_STRING_STYLE
)
from .styles import prompt_style, styled_block_fragments
from ..rendering.text_sanitize import sanitize_formatted_text
from frontends.tui.contracts.text import FormattedLine

TUI_APPROVAL_STYLE = Style.from_dict({
    "approval-card": "",
    "approval-question": "bold",
    "approval-context": "#7D8A98",
    "approval-field-label": "bold #AAB7C4",
    "approval-field-value": "#AAB7C4",
    "approval-permission-label": "",
    "approval-permission-value": "bold",
    "approval-meta": "dim #8896A5",
    "approval-omitted": "dim #8896A5",
    "approval-footer": "dim #8896A5",
    "approval-option": "#8B96A3",
    "approval-option-selected": "bold #5B8DEF",
    "approval-shortcut": "bold #C4CED8",
    "approval-shortcut-selected": "bold #C7F7FF",
    "approval-command": prompt_style(COMMAND_STYLE),
    "approval-command-head": prompt_style(COMMAND_HEAD_STYLE),
    "approval-command-flag": prompt_style(COMMAND_FLAG_STYLE),
    "approval-command-path": prompt_style(COMMAND_PATH_STYLE),
    "approval-command-string": prompt_style(COMMAND_STRING_STYLE),
    "approval-command-number": prompt_style(COMMAND_NUMBER_STYLE),
    "approval-command-operator": prompt_style(COMMAND_OPERATOR_STYLE),
    "approval-patch-action": "bold",
    "approval-patch-path": "",
    "approval-patch-count-add": "ansigreen",
    "approval-patch-count-remove": "ansired",
    "approval-patch-context": "",
    "approval-permission-rule": "cyan",
})


def tui_approval_content_lines(
    decisions: list[ApprovalDecisionValue],
    *,
    approval: ApprovalPresentation | dict[str, typing.Any],
    pending_count: int = 0,
    selected_index: int = 0,
    width: int | None = None,
    max_height: int | None = None,
) -> list[list[tuple[str, str]]]:
    """按当前可用空间生成审批面板内容行。"""
    approval = ensure_approval_presentation(approval)
    content_width = max(1, int(width or 80))

    question_lines = _wrap_fragment_line(
        [("class:approval-question", approval.context.prompt)],
        max_width=content_width,
    )

    if source_line := _approval_agent_source_line(
        approval,
        max_width=content_width,
    ):
        question_lines = [*question_lines, source_line]

    command_lines = _approval_operation_lines(
        approval,
        max_width=content_width,
    )

    detail_groups: list[list[list[tuple[str, str]]]] = []

    if environment := _approval_environment(approval):
        detail_groups.append(
            _approval_permission_field_lines(
                "Environment",
                environment,
                max_width=content_width,
            )
            if isinstance(approval, RequestPermissionsApprovalPresentation)
            else _approval_field_lines(
                "Environment",
                environment,
                max_width=content_width,
            )
        )

    justification = approval.context.justification
    if justification:
        detail_groups.append(
            _approval_permission_field_lines(
                "Reason",
                justification,
                max_width=content_width,
            )
            if isinstance(approval, RequestPermissionsApprovalPresentation)
            else _approval_field_lines(
                "Reason",
                justification,
                max_width=content_width,
            )
        )

    if isinstance(approval, RequestPermissionsApprovalPresentation):
        detail_groups.append(_approval_permission_rule_lines(
            approval.summary,
            max_width=content_width,
        ))
    elif (
        isinstance(approval, ExecApprovalPresentation)
        and approval.additional_permissions
    ):
        detail_groups.append(_approval_permission_rule_lines(
            _permission_request_summary(approval.additional_permissions),
            max_width=content_width,
        ))

    option_groups = _approval_option_groups(
        decisions,
        approval=approval,
        selected_index=selected_index,
        max_width=content_width,
    )

    footer_lines = _wrap_fragment_line(
        [(
            "class:approval-footer",
            (
                f"{pending_count} approval"
                f"{'s' if pending_count != 1 else ''} waiting · "
                if pending_count > 0
                else ""
            ) + "Press enter to confirm or ctrl + c to cancel",
        )],
        max_width=content_width,
    )

    return _fit_approval_sections(
        question_lines=question_lines,
        detail_groups=detail_groups,
        command_lines=command_lines,
        option_groups=option_groups,
        footer_lines=footer_lines,
        max_width=content_width,
        max_height=max_height,
    )


def _approval_environment(approval: ApprovalPresentation) -> str:
    """生成审批执行环境的展示名称。"""
    return approval.context.environment


def _approval_field_lines(
    label: str,
    value: str,
    *,
    max_width: int
) -> list[list[tuple[str, str]]]:
    """生成带标签且续行对齐的审批说明字段。"""
    return _wrap_prefixed_line(
        ("class:approval-field-label", f"{label}: "),
        [("class:approval-field-value", value)],
        max_width=max_width,
    )


def _approval_permission_rule_lines(
    summary: str,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """生成权限规则摘要行。"""
    return _wrap_prefixed_line(
        ("class:approval-permission-label", "Permission rule: "),
        [("class:approval-permission-rule", summary)],
        max_width=max_width,
    )


def _approval_permission_field_lines(
    label: str,
    value: str,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """生成权限卡片中的原生字段样式。"""
    return _wrap_prefixed_line(
        ("class:approval-permission-label", f"{label}: "),
        [("class:approval-permission-value", value)],
        max_width=max_width,
    )


def _permission_request_summary(permissions: dict[str, typing.Any]) -> str:
    """生成命令审批中附加权限的简要规则。"""
    parts: list[str] = []
    network = permissions.get("network")
    if isinstance(network, dict) and network.get("enabled") is True:
        parts.append("network")

    file_system = permissions.get("file_system")
    if isinstance(file_system, dict):
        for access in ("read", "write"):
            paths = file_system.get(access)
            if isinstance(paths, list) and paths:
                values = ", ".join(str(path) for path in paths)
                parts.append(f"{access} {values}")
        entries = file_system.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path") or "").strip()
                access = str(entry.get("access") or "").strip().casefold()
                if path and access in {"read", "write", "deny"}:
                    label = "deny read" if access == "deny" else access
                    parts.append(f"{label} {path}")
    return "; ".join(parts) or "additional permissions"


def _approval_agent_source_line(
    approval: ApprovalPresentation,
    *,
    max_width: int,
) -> list[tuple[str, str]]:
    """生成子执行线程审批请求的可信来源行。"""
    agent_id = approval.context.agent_id
    if not agent_id:
        return []

    agent_type = approval.context.agent_type
    return _clip_fragment_line(
        sanitize_formatted_text([(
            "class:approval-meta",
            f"Agent {agent_type} · {agent_id}",
        )]),
        max_width=max_width,
    )


def _approval_option_groups(
    decisions: list[ApprovalDecisionValue],
    *,
    approval: ApprovalPresentation,
    selected_index: int,
    max_width: int,
) -> list[list[list[tuple[str, str]]]]:
    """生成可按选项整体压缩的审批选项行。"""
    groups: list[list[list[tuple[str, str]]]] = []

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

        prefix = (label_style, f"{'›' if active else ' '} {index}. ")

        body = _decision_parts(
            decision,
            approval=approval,
            label_style=label_style,
            shortcut_style=shortcut_style,
        )
        groups.append(_wrap_prefixed_line(
            prefix,
            body,
            max_width=max_width,
        ))

    return groups


def _approval_operation_lines(
    approval: ApprovalPresentation,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """按审批展示类型生成卡片操作区域。"""
    if isinstance(approval, ApplyPatchApprovalPresentation):
        return _patch_approval_card_lines(approval, max_width=max_width)
    if isinstance(approval, ExecApprovalPresentation):
        return _exec_approval_card_lines(approval, max_width=max_width)
    if isinstance(approval, RequestPermissionsApprovalPresentation):
        return []
    return _tool_approval_card_lines(approval, max_width=max_width)


def _exec_approval_card_lines(
    approval: ExecApprovalPresentation,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """生成命令类审批卡片中的操作行。"""
    if approval.commands:
        return _single_command_lines(approval.commands[0], max_width=max_width)
    return _approval_summary_lines(approval.summary, max_width=max_width)


def _tool_approval_card_lines(
    approval: ToolApprovalPresentation,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """生成未细分工具审批卡片中的操作行。"""
    if approval.operations:
        return _single_command_lines(approval.operations[0], max_width=max_width)
    return _approval_summary_lines(approval.summary, max_width=max_width)


def _approval_summary_lines(
    summary: str,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """生成没有具体操作内容时的审批摘要。"""
    return _wrap_prefixed_line(
        ("class:approval-context", "$ "),
        _command_parts(summary),
        max_width=max_width,
    )


def approval_command_pager_lines(
    approval: ApprovalPresentation | dict[str, typing.Any],
    *,
    terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES,
) -> tuple[FormattedLine, ...]:
    """生成审批请求全屏预览所需的完整格式化行。"""
    approval = ensure_approval_presentation(approval)
    if isinstance(approval, ApplyPatchApprovalPresentation):
        return approval_patch_pager_lines(
            approval,
            terminal_capabilities=terminal_capabilities,
        )

    if isinstance(approval, RequestPermissionsApprovalPresentation):
        commands = (approval.summary,)
    else:
        commands = (
            approval.commands
            if isinstance(approval, ExecApprovalPresentation)
            else approval.operations
        )
    if not commands:
        commands = (approval.summary,)

    lines: list[FormattedLine] = []
    for command_index, command in enumerate(commands):
        if command_index:
            lines.append(())
        for raw_line in _command_raw_lines(command):
            parts = tuple(_command_parts(raw_line))
            lines.append(parts or (("class:approval-command", ""),))
    return tuple(lines)


def approval_pager_title(
    approval: ApprovalPresentation | dict[str, typing.Any],
) -> str:
    """返回审批全屏页面的操作标题。"""
    approval = ensure_approval_presentation(approval)
    return (
        "P A T C H"
        if isinstance(approval, ApplyPatchApprovalPresentation)
        else "E X E C"
    )


def approval_patch_pager_lines(
    approval: ApplyPatchApprovalPresentation | dict[str, typing.Any],
    *,
    terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES,
) -> tuple[FormattedLine, ...]:
    """生成补丁审批全屏页面的结构化差异行。"""
    presentation = ensure_approval_presentation(approval)
    if not isinstance(presentation, ApplyPatchApprovalPresentation):
        raise TypeError("patch pager requires an apply_patch approval")
    approval = presentation
    view = approval.patch_view
    if view is None or not view.files:
        return _raw_patch_pager_lines(approval)

    block = render_patch_view(
        view,
        terminal_width=None,
        terminal_capabilities=terminal_capabilities,
    )
    lines = _styled_block_lines(block)
    return lines or _raw_patch_pager_lines(approval)


def _patch_approval_card_lines(
    approval: ApplyPatchApprovalPresentation,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """生成底部审批卡中的补丁文件摘要。"""
    view = approval.patch_view
    if view is None or not view.files:
        return _single_patch_lines(approval.patch, max_width=max_width)

    lines: list[list[tuple[str, str]]] = []
    for index, file in enumerate(view.files):
        path = file.new_path or file.old_path
        action = {
            "add": "Added",
            "delete": "Deleted",
            "rename": "Renamed",
        }.get(file.action, "Edited")
        lines.extend(_wrap_fragment_line(
            _patch_file_summary_fragments(
                action,
                path,
                added=file.added,
                removed=file.removed,
            ),
            max_width=max_width,
        ))
        if index < len(view.files) - 1:
            lines.append([])
    return lines


def _patch_file_summary_fragments(
    action: str,
    path: str,
    *,
    added: int,
    removed: int,
) -> list[tuple[str, str]]:
    """生成补丁卡片中的动作、路径和增删统计片段。"""
    return [
        ("class:approval-patch-action", action),
        ("class:approval-patch-context", " "),
        ("class:approval-patch-path", path),
        ("class:approval-patch-context", " ("),
        ("class:approval-patch-count-add", f"+{added}"),
        ("class:approval-patch-context", " "),
        ("class:approval-patch-count-remove", f"-{removed}"),
        ("class:approval-patch-context", ")"),
    ]


def _styled_block_lines(block: StyledBlock) -> tuple[FormattedLine, ...]:
    """把结构化补丁渲染块拆分为静态页面可用的格式化行。"""
    rows: list[list[tuple[str, str]]] = [[]]
    for style, text in styled_block_fragments(block):
        chunks = str(text).split("\n")
        for index, chunk in enumerate(chunks):
            if chunk:
                rows[-1].append((style, chunk))
            if index < len(chunks) - 1:
                rows.append([])
    return tuple(tuple(row) for row in rows)


def _raw_patch_pager_lines(
    approval: ApplyPatchApprovalPresentation,
) -> tuple[FormattedLine, ...]:
    """生成没有结构化预览时的原始补丁页面行。"""
    return tuple(
        tuple(_command_parts(raw_line)) or (("class:approval-command", ""),)
        for raw_line in _command_raw_lines(approval.patch)
    )


def _single_patch_lines(
    patch: typing.Any,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """把补丁正文转换为多行审批预览。"""
    raw_lines = _command_raw_lines(patch)
    lines: list[list[tuple[str, str]]] = []
    for raw_line in raw_lines:
        lines.extend(_wrap_prefixed_line(
            ("class:approval-context", "  "),
            [("class:approval-command", raw_line)],
            max_width=max_width,
        ))
    return lines


def _single_command_lines(
    command: typing.Any,
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """把单项命令转换为多行审批预览。"""
    raw_lines = _command_raw_lines(command)

    lines: list[list[tuple[str, str]]] = []

    for index, raw_line in enumerate(raw_lines):
        lines.extend(_wrap_prefixed_line(
            (
                "class:approval-context",
                "$ " if index == 0 else "  ",
            ),
            _command_parts(raw_line),
            max_width=max_width,
        ))

    return lines


def _command_raw_lines(command: typing.Any) -> list[str]:
    """保留命令预览中的原始换行。"""
    if isinstance(command, (list, tuple)):
        text = " ".join(str(item) for item in command)
    else:
        text = str(command or "").strip()
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


def _fit_approval_sections(
    *,
    question_lines: list[list[tuple[str, str]]],
    detail_groups: list[list[list[tuple[str, str]]]],
    command_lines: list[list[tuple[str, str]]],
    option_groups: list[list[list[tuple[str, str]]]],
    footer_lines: list[list[tuple[str, str]]],
    max_width: int,
    max_height: int | None
) -> list[list[tuple[str, str]]]:
    """按高度预算组合询问、命令和审批选项。"""
    full = _approval_sections(
        question_lines,
        detail_groups,
        command_lines,
        option_groups,
        footer_lines,
    )
    if max_height is None:
        return full

    height  = max(1, int(max_height))
    if len(full) <= height:
        return full

    options = [line for group in option_groups for line in group]

    if len(options) + 2 > height:
        options = [
            _collapse_wrapped_group(group, max_width=max_width)
            for group in option_groups
        ]

    compact_details = [
        line
        for group in detail_groups
        for line in group
    ]

    variants = (
        (True, True, True),
        (False, True, True),
        (False, False, True),
        (False, False, False),
    )

    layout: tuple[
        bool,
        bool,
        bool,
        bool,
        list[list[tuple[str, str]]],
    ] = (False, False, False, False, [])
    found_layout = False

    for include_details in (bool(compact_details), False):
        detail_min = min(2, len(compact_details)) if include_details else 0
        for padding, include_footer, gaps in variants:
            footer = footer_lines[:1] if include_footer else []
            gap_count = _approval_gap_count(
                has_details=include_details,
                has_options=bool(options),
                has_footer=bool(footer),
                gaps=gaps,
            )
            minimum = (
                2
                + detail_min
                + len(options)
                + len(footer)
                + gap_count
                + _approval_padding_count(
                    padding=padding,
                    has_footer=bool(footer),
                )
            )
            if minimum <= height:
                layout = (
                    padding,
                    bool(footer),
                    gaps,
                    include_details,
                    [],
                )
                found_layout = True
                break
        if found_layout:
            break

    padding, include_footer, gaps, include_details, _unused = layout

    footer = footer_lines[:1] if include_footer else []

    gap_count = _approval_gap_count(
        has_details=include_details,
        has_options=bool(options),
        has_footer=bool(footer),
        gaps=gaps,
    )
    fixed_height = (
        len(options)
        + len(footer)
        + gap_count
        + _approval_padding_count(
            padding=padding,
            has_footer=bool(footer),
        )
    )
    text_budget = max(2, height - fixed_height)

    details: list[list[tuple[str, str]]] = []
    if include_details:
        detail_budget = min(
            len(compact_details),
            4,
            max(0, text_budget - 2),
        )
        details = _truncate_text_lines(
            compact_details,
            budget=detail_budget,
            max_width=max_width,
        )

    question_command_budget = max(2, text_budget - len(details))

    question_budget = min(
        len(question_lines),
        max(1, min(3, question_command_budget - 1)),
    )

    command_budget = max(1, question_command_budget - question_budget)

    question = _truncate_text_lines(
        question_lines,
        budget=question_budget,
        max_width=max_width,
    )
    command = _truncate_command_lines(
        command_lines,
        budget=command_budget,
        max_width=max_width,
    )

    return _assemble_approval_sections(
        question_lines=question,
        detail_lines=details,
        command_lines=command,
        option_lines=options,
        footer_lines=footer,
        padding=padding,
        gaps=gaps,
    )


def _approval_gap_count(
    *,
    has_details: bool,
    has_options: bool,
    has_footer: bool,
    gaps: bool
) -> int:
    """计算压缩布局中的 section 间隔行数。"""
    if not gaps:
        return 0
    return 1 + int(has_details) + int(has_options) + int(has_footer)


def _approval_padding_count(*, padding: bool, has_footer: bool) -> int:
    """计算卡片首尾留白占用的行数。"""
    if not padding:
        return 0
    return 1 if has_footer else 2


def _approval_sections(
    question_lines: list[list[tuple[str, str]]],
    detail_groups: list[list[list[tuple[str, str]]]],
    command_lines: list[list[tuple[str, str]]],
    option_groups: list[list[list[tuple[str, str]]]],
    footer_lines: list[list[tuple[str, str]]]
) -> list[list[tuple[str, str]]]:
    """组合不受高度限制的审批内容。"""
    details: list[list[tuple[str, str]]] = []
    for group in detail_groups:
        if details:
            details.append([])
        details.extend(group)

    return _assemble_approval_sections(
        question_lines=question_lines,
        detail_lines=details,
        command_lines=command_lines,
        option_lines=[line for group in option_groups for line in group],
        footer_lines=footer_lines,
        padding=True,
        gaps=True,
    )


def _assemble_approval_sections(
    *,
    question_lines: list[list[tuple[str, str]]],
    detail_lines: list[list[tuple[str, str]]],
    command_lines: list[list[tuple[str, str]]],
    option_lines: list[list[tuple[str, str]]],
    footer_lines: list[list[tuple[str, str]]],
    padding: bool,
    gaps: bool
) -> list[list[tuple[str, str]]]:
    """按确定的留白策略组装审批卡片。"""
    lines: list[list[tuple[str, str]]] = [[]] if padding else []
    lines.extend(question_lines)

    if gaps:
        lines.append([])
    if detail_lines:
        lines.extend(detail_lines)
        if gaps:
            lines.append([])

    lines.extend(command_lines)
    if option_lines:
        if gaps:
            lines.append([])
        lines.extend(option_lines)

    if footer_lines:
        if gaps:
            lines.append([])
        lines.extend(footer_lines)
    elif padding:
        lines.append([])

    return lines


def _truncate_command_lines(
    lines: list[list[tuple[str, str]]],
    *,
    budget: int,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """截断命令显示行，并在空间允许时同时保留头尾。"""
    limit = max(1, int(budget))

    if len(lines) <= limit:
        return lines
    if limit == 1:
        return [_line_with_suffix(
            lines[0],
            suffix=" …",
            suffix_style="class:approval-omitted",
            max_width=max_width,
        )]

    payload    = limit - 1
    head_count = max(1, (payload + 1) // 2)
    tail_count = max(0, payload - head_count)
    omitted    = len(lines) - head_count - tail_count
    noun       = "line" if omitted == 1 else "lines"

    marker = _clip_plain_line(
        f"  … {omitted} display {noun} omitted",
        style="class:approval-omitted",
        max_width=max_width,
    )

    tail = lines[-tail_count:] if tail_count else []
    return [*lines[:head_count], marker, *tail]


def _truncate_text_lines(
    lines: list[list[tuple[str, str]]],
    *,
    budget: int,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """把普通文本行截断到预算，并在末行标出省略。"""
    limit = max(0, int(budget))
    if len(lines) <= limit:
        return lines
    if limit <= 0:
        return []

    kept = lines[:limit]

    kept[-1] = _line_with_suffix(
        kept[-1],
        suffix=" …",
        suffix_style=kept[-1][-1][0] if kept[-1] else "",
        max_width=max_width,
    )
    return kept


def _collapse_wrapped_group(
    group: list[list[tuple[str, str]]],
    *,
    max_width: int,
) -> list[tuple[str, str]]:
    """把多行选项压缩成仍可操作的单行。"""
    if len(group) <= 1:
        return group[0] if group else []

    return _line_with_suffix(
        group[0],
        suffix=" …",
        suffix_style=group[0][-1][0] if group[0] else "",
        max_width=max_width,
    )


def _wrap_prefixed_line(
    prefix: tuple[str, str],
    body: list[tuple[str, str]],
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """按固定前缀宽度换行，并让续行正文保持对齐。"""
    prefix_width = max(0, get_cwidth(prefix[1]))
    body_width   = max(1, max_width - prefix_width)
    body_lines   = _wrap_fragment_line(body, max_width=body_width)
    continuation = (prefix[0], " " * prefix_width)

    return [
        [prefix if index == 0 else continuation, *line]
        for index, line in enumerate(body_lines)
    ]


def _wrap_fragment_line(
    parts: list[tuple[str, str]],
    *,
    max_width: int,
) -> list[list[tuple[str, str]]]:
    """按终端显示宽度换行格式化片段。"""
    parts = sanitize_formatted_text(parts)
    limit = max(1, int(max_width))

    lines: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]]     = []

    used: int = 0

    for style, text in parts:
        for token in re.findall(r"\s+|\S+", str(text)):
            token_width = max(0, get_cwidth(token))
            if token.isspace():
                if used and used + token_width > limit:
                    current, used = _flush_wrapped_line(lines, current)
                    continue
                if not used and lines:
                    continue
                current.append((style, token))
                used += token_width
                continue

            if (
                used
                and used + token_width > limit
                and (token_width <= limit or used >= limit)
            ):
                current, used = _flush_wrapped_line(lines, current)

            remaining = token
            while remaining:
                available = max(1, limit - used)
                chunk, remaining = _take_display_width(remaining, available)
                if not chunk:
                    chunk, remaining = remaining[0], remaining[1:]
                current.append((style, chunk))
                used += max(0, get_cwidth(chunk))
                if remaining:
                    current, used = _flush_wrapped_line(lines, current)

    if current or not lines:
        _flush_wrapped_line(lines, current)
    return lines


def _flush_wrapped_line(
    lines: list[list[tuple[str, str]]],
    current: list[tuple[str, str]]
) -> tuple[list[tuple[str, str]], int]:
    """提交当前换行片段并返回空的累积状态。"""
    lines.append(_trim_trailing_space(current))
    return [], 0


def _line_with_suffix(
    line: list[tuple[str, str]],
    *,
    suffix: str,
    suffix_style: str,
    max_width: int
) -> list[tuple[str, str]]:
    """裁剪单行并追加可见的省略标记。"""
    suffix_width = max(0, get_cwidth(suffix))
    body_width   = max(0, max_width - suffix_width)

    out = _clip_fragment_line(line, max_width=body_width)
    out.append((suffix_style, suffix))

    return out


def _clip_plain_line(
    text: str,
    *,
    style: str,
    max_width: int
) -> list[tuple[str, str]]:
    """把纯文本裁剪成单个显示行。"""
    return _clip_fragment_line([(style, text)], max_width=max_width)


def _clip_fragment_line(
    line: list[tuple[str, str]],
    *,
    max_width: int
) -> list[tuple[str, str]]:
    """按显示宽度裁剪格式化单行。"""
    remaining_width = max(0, int(max_width))

    out: list[tuple[str, str]] = []

    for style, text in line:
        if remaining_width <= 0:
            break
        chunk, _rest = _take_display_width(text, remaining_width)
        if chunk:
            out.append((style, chunk))
            remaining_width -= max(0, get_cwidth(chunk))

    return out


def _take_display_width(text: str, max_width: int) -> tuple[str, str]:
    """从文本开头取出不超过指定显示宽度的部分。"""
    limit = max(0, int(max_width))
    used  = 0
    end   = 0

    for index, char in enumerate(str(text)):
        char_width = max(0, get_cwidth(char))
        if used + char_width > limit:
            break
        used += char_width
        end = index + 1
    value = str(text)
    return value[:end], value[end:]


def _trim_trailing_space(
    line: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """移除自动换行产生的行尾空白。"""
    out = list(line)
    while out and not out[-1][1].rstrip():
        out.pop()
    if out:
        style, text = out[-1]
        trimmed = text.rstrip()
        if trimmed:
            out[-1] = style, trimmed
        else:
            out.pop()
    return out


def _decision_parts(
    decision: str,
    *,
    approval: ApprovalPresentation,
    label_style: str,
    shortcut_style: str,
) -> list[tuple[str, str]]:
    """生成审批选项标签和快捷键片段。"""
    amendment = (
        approval.amendment
        if isinstance(approval, ExecApprovalPresentation)
        else None
    )
    label    = approval_decision_label(decision, amendment=amendment)
    shortcut = DECISION_SHORTCUT_LABELS.get(decision, "")
    parts    = [(label_style, label)]

    if shortcut:
        parts.extend([
            (label_style, " ("),
            (shortcut_style, shortcut),
            (label_style, ")"),
        ])
    return parts


if __name__ == '__main__':
    pass
