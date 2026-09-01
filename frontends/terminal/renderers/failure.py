# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from frontends.terminal.text_layout import wrap_styled_line
from frontends.terminal.text import sanitize_terminal_text

FAILURE_DOT_STYLE = TextStyle(foreground="#FF5F5F")
FAILURE_TITLE_STYLE = TextStyle(foreground="#FF8A8A")
FAILURE_BRANCH_STYLE = TextStyle(foreground="#8FA4B8", dim=True)
FAILURE_MESSAGE_STYLE = TextStyle(foreground="#D98A8A")


def render_failure_title(phase: str) -> str:
    """生成 stream 生命周期失败标题。"""
    label = str(phase or "stream.failed").strip() or "stream.failed"
    return f"■ {label}"


def render_failure_text(phase: str, error: typing.Any) -> str:
    """生成 stream 生命周期失败块文本。"""
    title = render_failure_title(phase)
    message = _failure_message(error)
    return f"{title}\n  └ {message}" if message else title


def render_failure_display_parts(
    phase: str,
    error: typing.Any,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> list[TextSpan]:
    """把 stream 生命周期失败块转换为显示片段。"""
    title = render_failure_title(phase)
    message = _failure_message(error)

    parts: list[TextSpan] = [
        TextSpan("■", FAILURE_DOT_STYLE),
        TextSpan(title[1:], FAILURE_TITLE_STYLE),
    ]

    if message:
        message_parts = [TextSpan(message, FAILURE_MESSAGE_STYLE)]
        if isinstance(terminal_width, int) and terminal_width > 0:
            message_parts = wrap_styled_line(
                message_parts,
                terminal_width=terminal_width,
                first_prefix="  └ ",
                continuation_prefix=TextSpan("    ", FAILURE_BRANCH_STYLE),
                measure_width=measure_width,
            )
        parts.extend([
            TextSpan("\n"),
            TextSpan("  └ ", FAILURE_BRANCH_STYLE),
            *message_parts,
        ])

    return parts


def render_failure_block(
    phase: str,
    error: typing.Any,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> StyledBlock:
    """生成中立的 stream 生命周期失败展示块。"""
    parts = tuple(render_failure_display_parts(
        phase,
        error,
        terminal_width=terminal_width,
        measure_width=measure_width,
    ))

    return StyledBlock(
        plain_text=render_failure_text(phase, error),
        spans=parts,
        preserve_spans=True,
    )


def _failure_message(error: typing.Any) -> str:
    """压缩生命周期错误文本；不解析 shell stdout/stderr。"""
    message = sanitize_terminal_text(error).strip()
    if not message:
        return ""

    lines = [line.strip() for line in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [line for line in lines if line]

    if not lines:
        return ""

    if len(lines) == 1:
        return lines[0]

    return f"{lines[0]} ... (+{len(lines) - 1} lines)"


if __name__ == '__main__':
    pass
