# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

FAILURE_DOT_STYLE     = "bold #FF5F5F"
FAILURE_TITLE_STYLE   = "bold #FF8A8A"
FAILURE_BRANCH_STYLE  = "dim #8FA4B8"
FAILURE_MESSAGE_STYLE = "#D98A8A"


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
) -> list[dict[str, typing.Optional[str]]]:
    """把 stream 生命周期失败块转换为显示片段。"""
    title = render_failure_title(phase)
    message = _failure_message(error)

    parts: list[dict[str, typing.Optional[str]]] = [
        {"text": "■", "style": FAILURE_DOT_STYLE},
        {"text": title[1:], "style": FAILURE_TITLE_STYLE},
    ]
    if message:
        parts.extend([
            {"text": "\n", "style": None},
            {"text": "└ ", "style": FAILURE_BRANCH_STYLE},
            {"text": message, "style": FAILURE_MESSAGE_STYLE},
        ])
    return parts


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
