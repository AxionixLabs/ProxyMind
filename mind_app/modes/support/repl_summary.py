# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from rich.text import Text
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)

COMMAND_SUMMARY_DEFAULT_WIDTH: int = 100
COMMAND_SUMMARY_COMMAND_MAX: int   = 72
COMMAND_SUMMARY_LINE_MAX: int      = 120


@dataclass(frozen=True, slots=True)
class CommandSummary(object):
    """描述命令面板结束后的统一摘要内容。"""
    kind: str
    command: str
    suffix: str = ""
    lines: tuple[str, ...] = ()


def render_command_summary(
    application: ApplicationSink,
    summary: CommandSummary
) -> None:
    """渲染命令面板最终摘要。"""
    renderable = command_summary_text(
        summary,
        terminal_width=application.viewport.width
    )
    renderable.rstrip()
    application.emit(ApplicationView(
        type="repl.command_summary",
        renderable=renderable,
    ))


def command_summary_text(
    summary: CommandSummary,
    *,
    terminal_width: int | None = None
) -> Text:
    """生成命令面板最终摘要文本。"""
    renderable = Text()

    for text, style in command_summary_title_parts(
        summary,
        terminal_width=terminal_width
    ):
        renderable.append(text, style=style)

    for line in summary.lines:
        renderable.append("\n")
        renderable.append("  ", style="dim #7F8C9A")
        renderable.append(
            _summary_line_text(line, terminal_width=terminal_width),
            style="dim #A8B1BB"
        )

    return renderable


def command_summary_title_parts(
    summary: CommandSummary,
    *,
    terminal_width: int | None = None
) -> list[tuple[str, str]]:
    """返回命令面板摘要标题分段。"""
    kind   = str(summary.kind or "Command").strip() or "Command"
    suffix = str(summary.suffix or "")

    command = _summary_command_text(
        summary.command,
        kind=kind,
        suffix=suffix,
        terminal_width=terminal_width
    )

    parts = [
        ("• ", "dim #7F8C9A"),
        (kind, "bold #8FC7EA"),
        (" ", "dim #7F8C9A"),
        (command, "bold #F4F7FA"),
    ]

    if suffix:
        parts.append((suffix, "dim #7F8C9A"))

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

    width        = int(terminal_width or COMMAND_SUMMARY_DEFAULT_WIDTH)
    prefix_width = len(f"• {kind} ")

    available = min(
        COMMAND_SUMMARY_COMMAND_MAX,
        max(12, width - prefix_width - len(suffix))
    )

    return _clip_inline(text, available)


def _summary_line_text(
    line: typing.Any,
    *,
    terminal_width: int | None
) -> str:
    """返回适合摘要正文展示的单行文本。"""
    width     = int(terminal_width or COMMAND_SUMMARY_DEFAULT_WIDTH)
    available = min(COMMAND_SUMMARY_LINE_MAX, max(12, width - 2))
    return _clip_inline(line, available)


def _clip_inline(value: typing.Any, limit: int) -> str:
    """裁剪单行文本。"""
    text = " ".join(str(value or "").split())
    size = max(1, int(limit or 1))
    if len(text) <= size:
        return text
    if size <= 1:
        return "…"
    return f"{text[:size - 1]}…"


if __name__ == '__main__':
    pass
