# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from enum import Enum
from mind_app.presentation.terminal_text import sanitize_terminal_text
from metadata import const
from prompt_toolkit.utils import get_cwidth
from ...contracts.text import FormattedText
from ..fragments import clip_fragments


class FooterMode(str, Enum):
    """描述输入 footer 当前采用的互斥展示模式。"""
    DEFAULT = "default"
    HISTORY_BACKTRACK = "history_backtrack"
    EXIT_ARMED = "exit_armed"
    QUEUE_SUBMISSION = "queue_submission"
    HIDDEN = "hidden"


def resolve_footer_mode(
    *,
    history_backtrack_primed: bool,
    exit_armed: bool,
    queue_submission_hint_visible: bool,
    submission_pending: bool,
) -> FooterMode:
    """按 footer 展示优先级返回唯一模式。"""
    if history_backtrack_primed:
        return FooterMode.HISTORY_BACKTRACK
    if exit_armed:
        return FooterMode.EXIT_ARMED
    if queue_submission_hint_visible:
        return FooterMode.QUEUE_SUBMISSION
    if submission_pending:
        return FooterMode.HIDDEN
    return FooterMode.DEFAULT


def footer_fragments(
    *,
    mode: FooterMode,
    width: int,
    brand_color: str = "",
    mailbox_label: str = "",
    model_label: str | None = "",
    permissions_label: str = "",
    workspace_label: str = "",
) -> FormattedText:
    """生成指定模式下的输入 footer 片段。"""
    if mode is FooterMode.HISTORY_BACKTRACK:
        return [
            ("class:footer.exit-key", "Esc"),
            ("class:footer.exit-hint", " again to edit previous message"),
        ]
    if mode is FooterMode.EXIT_ARMED:
        return [
            ("class:footer.exit-key", "Ctrl + C"),
            ("class:footer.exit-hint", " again to exit"),
        ]
    if mode is FooterMode.QUEUE_SUBMISSION:
        full_hint = "  tab to queue message"
        hint = full_hint if get_cwidth(full_hint) <= width else "  tab to queue"
        return [("class:footer.queue-hint", hint)]
    if mode is FooterMode.HIDDEN:
        return []

    permissions = sanitize_terminal_text(permissions_label).strip()
    access_style = (
        "class:footer.access.full"
        if permissions.lower() == "full access"
        else "class:footer.access"
    )
    parts: FormattedText = [(f"fg:{brand_color}", const.APP_DESC)]
    values = (
        ("class:footer.mailbox", mailbox_label),
        ("class:footer.model", model_label or "-"),
        (access_style, permissions),
        ("class:footer.workspace", workspace_label),
    )
    for style, value in values:
        text = sanitize_terminal_text(value).strip()
        if text:
            parts.extend([
                ("class:footer.separator", " · "),
                (style, text),
            ])
    return clip_fragments(parts, width=width)


def queued_row_budget(
    *,
    pending_active: bool,
    queued_active: bool,
    max_height: int,
) -> tuple[int, int]:
    """返回 steer 和普通排队消息各自的最大行数。"""
    limit = max(0, int(max_height))
    if pending_active and queued_active:
        pending_rows = min(3, limit)
        return pending_rows, max(0, limit - pending_rows)
    return limit, limit


def join_queued_fragments(
    pending: FormattedText,
    queued: FormattedText,
    *,
    show_leading_gap: bool,
) -> FormattedText:
    """合并两类排队消息并按需加入活动区间距。"""
    if pending and queued:
        fragments = [*pending, ("", "\n"), *queued]
    else:
        fragments = pending or queued
    if fragments and show_leading_gap:
        return [("", "\n"), *fragments]
    return fragments


def input_prompt_fragments(*, shell_mode: bool) -> FormattedText:
    """生成输入区域首行的独立模式提示符。"""
    return [
        ("class:shell-escape", "!")
        if shell_mode
        else ("class:prompt.kicker", "›")
    ]


def placeholder_fragments(text: str) -> FormattedText:
    """生成输入区域中为光标保留首格的占位文本。"""
    return [("class:placeholder", f" {text}")]


def completion_hint_fragments(*, left_padding: int) -> FormattedText:
    """生成 skill 补全底部提示。"""
    padding = " " * max(0, int(left_padding))
    return [
        ("class:token-menu.hint", f"{padding}Press "),
        ("class:token-menu.hint.key", "enter"),
        ("class:token-menu.hint", " to insert or "),
        ("class:token-menu.hint.key", "esc"),
        ("class:token-menu.hint", " to close"),
    ]


def mention_completion_hint_fragments(
    *,
    left_padding: int,
    width: int,
    active_mode: str
) -> FormattedText:
    """生成 `@` popup 使用的搜索模式提示行。"""
    left = (
        f"{' ' * max(0, int(left_padding))}"
        "enter insert · esc close · ←/→ switch search modes"
    )

    modes = ("All Results", "Filesystem Only", "Plugins")
    right_parts: list[tuple[str, str]] = []
    right_text = ""
    slot_widths = {
        mode: get_cwidth(f"[{mode}]")
        for mode in modes
    }

    for index, mode in enumerate(modes):
        if index:
            right_parts.append(("class:tui-menu.footer.right", "  "))
        active = mode == active_mode
        label = f"[{mode}]" if active else mode.center(slot_widths[mode])
        style = (
            "class:tui-menu.footer.right.plugins.current"
            if active and mode == "Plugins"
            else
            "class:tui-menu.footer.right.current"
            if active
            else "class:tui-menu.footer.right"
        )
        right_parts.append((style, label))
        right_text += ("  " if index else "") + label

    gap = max(1, int(width) - get_cwidth(left) - get_cwidth(right_text))

    left_fragments: FormattedText = [
        ("class:tui-menu.footer.hint", " " * max(0, int(left_padding))),
        ("class:tui-menu.footer.hint", "enter"),
        ("class:tui-menu.footer.hint", " insert · "),
        ("class:tui-menu.footer.hint", "esc"),
        ("class:tui-menu.footer.hint", " close · ←/→ switch search modes"),
    ]

    return [
        *left_fragments,
        ("class:token-menu.hint", " " * gap),
        *right_parts,
    ]


def completion_empty_fragments(
    *,
    left_padding: int,
    mention: bool = False,
    message: str = "no matches",
) -> FormattedText:
    """生成 completion 没有匹配项时的单行提示。"""
    return [(
        "class:completion-menu.empty.mention"
        if mention else "class:completion-menu.empty",
        f"{' ' * max(0, int(left_padding))}{message}",
    )]


def completion_candidate_fragments(
    *,
    display_text: str,
    display_meta_text: str,
    is_slash_command: bool,
    left_padding: int,
    column_min_width: int
) -> FormattedText:
    """生成精确匹配的单个 completion 候选行。"""
    padding_width = max(0, int(left_padding))
    display_width = get_cwidth(display_text)

    command_width = max(
        max(0, int(column_min_width)),
        display_width + padding_width + 1,
    )

    command_padding = " " * (
        command_width - display_width - padding_width
    )

    fragments: FormattedText = [(
        (
            "class:token-menu.command.current"
            if is_slash_command
            else "class:completion-menu.completion.current"
        ),
        f"{' ' * padding_width}{display_text}{command_padding}",
    )]

    if display_meta_text:
        fragments.append((
            (
                "class:token-menu.meta.command.current"
                if is_slash_command
                else "class:completion-menu.meta.completion.current"
            ),
            f" {display_meta_text} ",
        ))

    return fragments


if __name__ == '__main__':
    pass
