# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from pathlib import Path
from prompt_toolkit.formatted_text import ANSI
from infrastructure.platform.git_diff import (
    WorkspaceDiffError,
    WorkspaceDiffService,
    WorkspaceDiffState
)
from mind_app.presentation.terminal_text import sanitize_terminal_text
from ..contracts.pager import StaticPagerRequest
from ..contracts.text import FormattedLine
from ..runtime.ports import StaticPagerRuntimePort

if typing.TYPE_CHECKING:
    from ...controller import Mind


async def show_workspace_diff(
    runtime: StaticPagerRuntimePort,
    controller: "Mind",
    *,
    cwd: Path,
) -> None:
    """计算当前 Git 工作区差异并打开全屏静态页面。"""
    cwd = Path(cwd).resolve()
    try:
        result = await WorkspaceDiffService().compute(cwd)
    except WorkspaceDiffError as error:
        text = f"Failed to compute diff: {_error_detail(error)}"
    else:
        text = (
            result.text
            if result.state is WorkspaceDiffState.READY
            else "`/diff` \N{EM DASH} _not inside a git repository_"
        )

    current_cwd = Path(controller.history_workspace).resolve()
    if os.path.normcase(str(current_cwd)) != os.path.normcase(str(cwd)):
        return None

    runtime.open_static_pager(StaticPagerRequest(
        title="D I F F",
        lines=diff_pager_lines(text),
    ))


def diff_pager_lines(diff_text: str) -> tuple[FormattedLine, ...]:
    """把 ANSI Git 差异转换为不可执行的格式化页面行。"""
    text = str(diff_text or "")
    if not text.strip():
        return ((
            ("class:static-pager.empty", "No changes detected."),
        ),)

    lines: list[FormattedLine] = []
    for raw_line in text.splitlines():
        parsed = ANSI(_safe_sgr_ansi(raw_line)).__pt_formatted_text__()
        fragments: list[tuple[str, str]] = []
        for style, value, *_handler in parsed:
            safe_value = sanitize_terminal_text(value)
            if not safe_value:
                continue
            if fragments and fragments[-1][0] == style:
                previous_style, previous_text = fragments[-1]
                fragments[-1] = previous_style, previous_text + safe_value
            else:
                fragments.append((style, safe_value))
        lines.append(tuple(fragments))
    return tuple(lines)


def _safe_sgr_ansi(value: str) -> str:
    """只保留 SGR 样式并丢弃其他终端控制序列。"""
    text = str(value or "")
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            index = _consume_escape_sequence(text, index, out)
            continue
        if char == "\x9b":
            index = _consume_csi(text, index + 1, out, prefix="\x1b[")
            continue
        if char in {"\x90", "\x98", "\x9d", "\x9e", "\x9f"}:
            index = _consume_control_string(text, index + 1)
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _consume_escape_sequence(text: str, start: int, out: list[str]) -> int:
    """消费一个七位终端转义序列，并按白名单保留 SGR。"""
    if start + 1 >= len(text):
        return len(text)
    marker = text[start + 1]
    if marker == "[":
        return _consume_csi(text, start + 2, out, prefix="\x1b[")
    if marker in {"P", "X", "]", "^", "_"}:
        return _consume_control_string(text, start + 2)

    index = start + 1
    while index < len(text):
        current = text[index]
        index += 1
        if "\x30" <= current <= "\x7e":
            break
    return index


def _consume_csi(
    text: str,
    start: int,
    out: list[str],
    *,
    prefix: str,
) -> int:
    """消费 CSI，并仅保留数字参数组成的 SGR 序列。"""
    index = start
    while index < len(text):
        final = text[index]
        if "\x40" <= final <= "\x7e":
            parameters = text[start:index]
            if final == "m" and all(
                char.isdigit() or char in {";", ":"}
                for char in parameters
            ):
                out.append(f"{prefix}{parameters}m")
            return index + 1
        index += 1
    return len(text)


def _consume_control_string(text: str, start: int) -> int:
    """消费以 BEL、ST 或 ESC 反斜杠结束的控制字符串。"""
    index = start
    while index < len(text):
        char = text[index]
        if char in {"\x07", "\x9c"}:
            return index + 1
        if char == "\x1b" and index + 1 < len(text) and text[index + 1] == "\\":
            return index + 2
        index += 1
    return len(text)


def _error_detail(error: BaseException) -> str:
    """返回适合静态页面展示的单行错误详情。"""
    detail = sanitize_terminal_text(str(error)).strip()
    return detail or type(error).__name__


if __name__ == '__main__':
    pass
