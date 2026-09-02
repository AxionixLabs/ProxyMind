# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from prompt_toolkit.utils import get_cwidth

from frontends.tui.contracts.text import FormattedText
from ..fragments import clip_text


def transcript_header_fragments(
    *,
    width: int,
    raw_mode: bool,
) -> FormattedText:
    """生成完整记录 overlay 的标题行。"""
    render_width = max(0, int(width))
    pattern = ("/ " * ((render_width + 1) // 2))[:render_width]
    title = (
        "/ R A W   T R A N S C R I P T"
        if raw_mode
        else "/ T R A N S C R I P T"
    )
    if len(title) >= render_width:
        return [("class:transcript.overlay.title", title[:render_width])]
    return [
        ("class:transcript.overlay.title", title),
        ("class:transcript.overlay.rule", pattern[len(title):]),
    ]


def static_pager_header_fragments(
    *,
    width: int,
    title: str
) -> FormattedText:
    """生成静态页面标题行。"""
    render_width = max(0, int(width))
    pattern = ("/ " * ((render_width + 1) // 2))[:render_width]
    heading = clip_text(f"/ {title}", width=render_width)
    heading_width = get_cwidth(heading)

    return [
        ("class:static-pager.title", heading),
        ("class:static-pager.rule", pattern[heading_width:]),
    ]


def static_pager_separator_fragments(
    *,
    width: int,
    percentage: int
) -> FormattedText:
    """生成包含滚动百分比的静态页面分隔线。"""
    render_width = max(0, int(width))

    progress = clip_text(
        f" {max(0, min(100, int(percentage)))}% ",
        width=render_width,
    )

    progress_width = get_cwidth(progress)
    prefix_width = max(0, render_width - progress_width - 1)

    return [
        ("class:static-pager.rule", "─" * prefix_width),
        ("class:static-pager.progress", progress),
        (
            "class:static-pager.rule",
            "─" * max(0, render_width - prefix_width - progress_width),
        ),
    ]


def transcript_separator_fragments(
    *,
    width: int,
    percentage: int
) -> FormattedText:
    """生成包含滚动百分比的完整记录分隔线。"""
    render_width = max(0, int(width))
    progress = f" {percentage}% "
    progress_start = max(0, render_width - len(progress) - 1)

    return [
        ("class:transcript.overlay.rule", "─" * progress_start),
        ("class:transcript.overlay.progress", progress),
        (
            "class:transcript.overlay.rule",
            "─" * max(0, render_width - progress_start - len(progress)),
        ),
    ]


def mailbox_header_fragments(
    *,
    width: int,
    pending_count_label: str,
    listener_active: bool,
) -> FormattedText:
    """生成收件箱 overlay 的标题、数量和监听状态。"""
    render_width = max(0, int(width))
    state = "listening" if listener_active else "stopped"
    status = f" {pending_count_label} pending · {state} "
    title = "/ M A I L B O X "
    pattern = ("/ " * ((render_width + 1) // 2))[:render_width]
    available = max(0, render_width - get_cwidth(status))
    heading = clip_text(title, width=available)
    fill_width = max(0, available - get_cwidth(heading))

    return [
        ("class:mailbox.title", heading),
        ("class:mailbox.rule", pattern[:fill_width]),
        (
            "class:mailbox.status",
            clip_text(
                status,
                width=render_width - get_cwidth(heading) - fill_width,
            ),
        ),
    ]


def mailbox_separator_fragments(
    *,
    width: int,
    current_label: str,
    total_label: str,
    has_multiple: bool
) -> FormattedText:
    """生成包含消息正文页码的收件箱分隔线。"""
    render_width = max(0, int(width))

    progress = (
        f" {current_label}/{total_label} "
        if has_multiple
        else ""
    )

    progress_width = get_cwidth(progress)
    prefix_width = max(0, render_width - progress_width - int(bool(progress)))

    return [
        ("class:mailbox.filler", "─" * prefix_width),
        ("class:mailbox.progress", progress),
        (
            "class:mailbox.filler",
            "─" * max(0, render_width - prefix_width - progress_width),
        ),
    ]


if __name__ == '__main__':
    pass
