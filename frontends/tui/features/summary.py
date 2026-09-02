# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from prompt_toolkit.utils import get_cwidth

from agent.ports.presentation import (
    ApplicationSink,
    ApplicationView
)
from agent.ports.presentation import TextStyle
from ..core.models import FragmentBlock
from ..core.styles import prompt_style
from ..rendering.fragments import clip_text

COMMAND_SUMMARY_DEFAULT_WIDTH: int = 100
COMMAND_SUMMARY_COMMAND_MAX: int = 72
COMMAND_SUMMARY_LINE_MAX: int = 120


@dataclass(frozen=True, slots=True)
class CommandSummary(object):
    """描述命令面板结束后的统一摘要内容。"""
    kind: str
    command: str
    suffix: str = ""
    lines: tuple[str, ...] = ()


def render_command_summary(
    application: ApplicationSink,
    summary: CommandSummary,
    *,
    line_prefix: str = "    ",
    first_line_prefix: str | None = None
) -> None:
    """渲染命令面板最终摘要。"""
    renderable = command_summary_text(
        summary,
        terminal_width=application.viewport.width,
        line_prefix=line_prefix,
        first_line_prefix=first_line_prefix,
    )
    application.emit(ApplicationView(
        type="tui.command_summary",
        renderable=renderable,
    ))


def command_summary_text(
    summary: CommandSummary,
    *,
    terminal_width: int | None = None,
    line_prefix: str = "    ",
    first_line_prefix: str | None = None
) -> FragmentBlock:
    """生成命令面板最终摘要文本。"""
    fragments: list[tuple[str, str]] = []

    for text, style in command_summary_title_parts(
        summary,
        terminal_width=terminal_width
    ):
        fragments.append((style, text))

    for index, line in enumerate(summary.lines):
        prefix = (
            first_line_prefix
            if index == 0 and first_line_prefix is not None
            else line_prefix
        )
        fragments.extend([
            ("", "\n"),
            (
                prompt_style(TextStyle(foreground="#7F8C9A", dim=True)),
                prefix,
            ),
            (
                prompt_style(TextStyle(foreground="#A8B1BB", dim=True)),
                _summary_line_text(
                    line,
                    terminal_width=terminal_width,
                    line_prefix=prefix,
                ),
            ),
        ])

    return FragmentBlock(tuple(fragments))


def command_summary_title_parts(
    summary: CommandSummary,
    *,
    terminal_width: int | None = None
) -> list[tuple[str, str]]:
    """返回命令面板摘要标题分段。"""
    kind = str(summary.kind or "Command").strip() or "Command"
    width = int(terminal_width or COMMAND_SUMMARY_DEFAULT_WIDTH)

    suffix = clip_text(
        str(summary.suffix or ""),
        width=max(0, width - get_cwidth(f"• {kind} ") - 1),
    )

    command = _summary_command_text(
        summary.command,
        kind=kind,
        suffix=suffix,
        terminal_width=terminal_width
    )

    parts = [
        ("• ", prompt_style(TextStyle(foreground="#7F8C9A", dim=True))),
        (kind, prompt_style(TextStyle(foreground="#8FC7EA", bold=True))),
        (" ", prompt_style(TextStyle(foreground="#7F8C9A", dim=True))),
        (command, prompt_style(TextStyle(foreground="#F4F7FA"))),
    ]

    if suffix:
        parts.append((suffix, prompt_style(TextStyle(foreground="#7F8C9A", dim=True))))

    return parts


def _summary_command_text(
    command: typing.Any,
    *,
    kind: str,
    suffix: str,
    terminal_width: int | None
) -> str:
    """返回适合摘要标题展示的命令文本。"""
    fallback = f"{str(kind or 'command').lower()} command"

    text = str(command or fallback).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        text = fallback

    width = int(terminal_width or COMMAND_SUMMARY_DEFAULT_WIDTH)
    prefix_width = get_cwidth(f"• {kind} ")

    available = min(
        COMMAND_SUMMARY_COMMAND_MAX,
        max(1, width - prefix_width - get_cwidth(suffix))
    )

    return _clip_inline(text, available)


def _summary_line_text(
    line: typing.Any,
    *,
    terminal_width: int | None,
    line_prefix: str,
) -> str:
    """返回适合摘要正文展示的单行文本。"""
    width = int(terminal_width or COMMAND_SUMMARY_DEFAULT_WIDTH)
    available = min(
        COMMAND_SUMMARY_LINE_MAX,
        max(0, width - get_cwidth(line_prefix)),
    )

    return _clip_inline(line, available) if available > 0 else ""


def _clip_inline(value: typing.Any, limit: int) -> str:
    """裁剪单行文本。"""
    text = " ".join(str(value or "").split())
    return clip_text(text, width=max(1, int(limit or 1)))


if __name__ == '__main__':
    pass
