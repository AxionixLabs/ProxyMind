# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)

FAILURE_DOT_STYLE     = TextStyle(foreground="#FF5F5F", bold=True)
FAILURE_TITLE_STYLE   = TextStyle(foreground="#FF8A8A", bold=True)
FAILURE_BRANCH_STYLE  = TextStyle(foreground="#8FA4B8", dim=True)
FAILURE_MESSAGE_STYLE = TextStyle(foreground="#D98A8A")


def render_failure_title(phase: str) -> str:
    """生成 stream 生命周期失败标题。"""
    label = str(phase or "stream.failed").strip() or "stream.failed"
    return f"■ {label}"


def render_failure_text(phase: str, error: typing.Any) -> str:
    """生成 stream 生命周期失败块文本。"""
    title = render_failure_title(phase)
    message = _failure_message(error)
    return f"{title}\n└ {message}" if message else title


def render_failure_display_parts(
    phase: str,
    error: typing.Any
) -> list[TextSpan]:
    """把 stream 生命周期失败块转换为显示片段。"""
    title = render_failure_title(phase)
    message = _failure_message(error)

    parts: list[TextSpan] = [
        TextSpan("■", FAILURE_DOT_STYLE),
        TextSpan(title[1:], FAILURE_TITLE_STYLE),
    ]
    if message:
        parts.extend([
            TextSpan("\n"),
            TextSpan("└ ", FAILURE_BRANCH_STYLE),
            TextSpan(message, FAILURE_MESSAGE_STYLE),
        ])
    return parts


def render_failure_block(phase: str, error: typing.Any) -> StyledBlock:
    """生成中立的 stream 生命周期失败展示块。"""
    parts = tuple(render_failure_display_parts(phase, error))
    return StyledBlock(
        plain_text="".join(part.text for part in parts),
        spans=parts,
    )


def _failure_message(error: typing.Any) -> str:
    """压缩生命周期错误文本；不解析 shell stdout/stderr。"""
    message = "" if error is None else str(error).strip()
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
