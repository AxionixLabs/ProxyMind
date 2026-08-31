# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.views import TracePreview
from mind_app.presentation.terminal_text import (
    sanitize_terminal_line,
    sanitize_terminal_text,
)

MISSING = object()

MAX_PREVIEW_LINES         = 8
SCREEN_PREVIEW_LINES      = 5
MAX_PREVIEW_WIDTH         = 120
MAX_CODE_PREVIEW_LINES    = 48
SCREEN_CODE_PREVIEW_LINES = 18


def _short_text(value: typing.Any, limit: int = 120) -> str:
    """把任意值压缩为单行短文本。"""
    text = sanitize_terminal_line(value)
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


def _short_line(value: typing.Any, limit: int = 120) -> str:
    """截断单行文本并保留原有空白结构。"""
    text = sanitize_terminal_text(value).rstrip()
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


def _normalize_preview_lines(value: typing.Any) -> list[str]:
    """把预览内容归一化为按行拆分的文本列表。"""
    text = sanitize_terminal_text(value).strip("\n")
    if not text:
        return []
    return text.split("\n")


def _format_preview_lines(lines: list[str], *, max_lines: int) -> tuple[str, int]:
    """按行数和宽度限制格式化预览文本。"""
    clipped = [
        _short_line(line, MAX_PREVIEW_WIDTH)
        for line in lines[:max_lines]
    ]
    omitted = max(0, len(lines) - max_lines)
    if omitted:
        clipped.append(f"… +{omitted} lines")
    return "\n".join(clipped), omitted


def _format_middle_preview_lines(
    lines: list[str],
    *,
    max_lines: int
) -> tuple[str, int]:
    """保留头尾并折叠中间行，返回预览文本和省略数量。"""
    clipped = [
        _short_line(line, MAX_PREVIEW_WIDTH)
        for line in lines
    ]
    limit = max(1, int(max_lines))
    if len(clipped) <= limit:
        return "\n".join(clipped), 0

    retained   = limit - 1
    head_count = retained // 2
    tail_count = retained - head_count
    omitted    = len(clipped) - retained

    folded = [
        *clipped[:head_count],
        f"… +{omitted} lines",
        *clipped[-tail_count:],
    ]

    return "\n".join(folded), omitted


def _preview_text(value: typing.Any, *, max_lines: int = MAX_PREVIEW_LINES) -> str:
    """生成普通文本预览。"""
    screen, _ = _format_preview_lines(_normalize_preview_lines(value), max_lines=max_lines)
    return screen


def _trace_preview_from_lines(lines: list[str]) -> TracePreview:
    """从文本行生成普通轨迹预览。"""
    full, _ = _format_preview_lines(lines, max_lines=MAX_PREVIEW_LINES)
    screen, omitted = _format_preview_lines(lines, max_lines=SCREEN_PREVIEW_LINES)
    return TracePreview(full=full, screen=screen, omitted_lines=omitted)


def _plain_trace_preview_from_lines(lines: list[str]) -> TracePreview:
    """从文本行生成纯文本轨迹预览，不做路径/摘要结构识别。"""
    full, _ = _format_preview_lines(lines, max_lines=MAX_PREVIEW_LINES)
    screen, omitted = _format_preview_lines(lines, max_lines=SCREEN_PREVIEW_LINES)
    return TracePreview(full=full, screen=screen, omitted_lines=omitted, kind="plain")


def _shell_trace_preview_from_lines(
    lines: list[str],
    *,
    kind: str = "text"
) -> TracePreview:
    """生成 Shell 输出的头尾折叠预览。"""
    full, _ = _format_middle_preview_lines(lines, max_lines=MAX_PREVIEW_LINES)

    screen, omitted = _format_middle_preview_lines(
        lines,
        max_lines=SCREEN_PREVIEW_LINES,
    )

    return TracePreview(
        full=full,
        screen=screen,
        omitted_lines=omitted,
        kind=kind,
    )


def _terminal_input_preview_from_lines(lines: list[str]) -> TracePreview:
    """从标准输入行生成终端交互预览。"""
    full, _ = _format_preview_lines(lines, max_lines=MAX_PREVIEW_LINES)
    screen, omitted = _format_preview_lines(lines, max_lines=SCREEN_PREVIEW_LINES)

    return TracePreview(
        full=full,
        screen=screen,
        omitted_lines=omitted,
        kind="terminal_input",
    )


def _trace_code_preview_from_lines(lines: list[str]) -> TracePreview:
    """从代码行生成轨迹预览。"""
    full, _ = _format_preview_lines(lines, max_lines=MAX_CODE_PREVIEW_LINES)
    screen, omitted = _format_preview_lines(lines, max_lines=SCREEN_CODE_PREVIEW_LINES)

    return TracePreview(
        full=full,
        screen=screen,
        omitted_lines=omitted,
        kind="code",
    )


def _result_payload(data: typing.Any) -> dict[str, typing.Any]:
    """从工具结果中提取可用于渲染的 data 载荷。"""
    if not isinstance(data, dict):
        return {}

    results = data.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            item_data = item.get("data")
            if isinstance(item_data, dict):
                return item_data

    return data


def _summary_lines(*items: tuple[str, typing.Any]) -> list[str]:
    """把键值对转换为摘要行。"""
    lines: list[str] = []
    for label, value in items:
        if value is None:
            text = ""
        elif isinstance(value, bool):
            text = str(value)
        else:
            text = str(value or "").strip()
        if text:
            lines.append(f"{label}: {text}")
    return lines


if __name__ == '__main__':
    pass
