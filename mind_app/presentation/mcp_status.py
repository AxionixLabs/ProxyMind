# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.mcp_status import McpStatusView
from .models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from .styles import (
    ERROR_DOT_STYLE,
    ERROR_PREVIEW_MESSAGE_STYLE,
    PREVIEW_MORE_STYLE,
    SUCCESS_DOT_STYLE
)
from .terminal_text import sanitize_terminal_text
from .text_layout import layout_styled_line

MCP_STATUS_BODY_STYLE    = TextStyle(foreground="#DDE7EF")
MCP_STATUS_WARNING_STYLE = TextStyle(foreground="#F59E0B")


def render_mcp_status_block(
    view: McpStatusView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把已结束的 MCP 状态转换为稳定展示块。"""
    summary = str(view.summary or "").strip()
    if not view.done or not summary:
        return StyledBlock(plain_text="")

    marker_style = {
        "ready": SUCCESS_DOT_STYLE,
        "warning": MCP_STATUS_WARNING_STYLE,
        "failed": ERROR_DOT_STYLE,
    }.get(view.level, MCP_STATUS_BODY_STYLE)
    summary_style = (
        ERROR_PREVIEW_MESSAGE_STYLE
        if view.level == "failed"
        else MCP_STATUS_BODY_STYLE
    )

    summary_lines = _content_lines(summary)
    if not summary_lines:
        return StyledBlock(plain_text="")
    parts = layout_styled_line(
        [TextSpan(summary_lines[0], summary_style)],
        first_prefix=TextSpan("■ ", marker_style),
        continuation_prefix=TextSpan("  ", summary_style),
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    for line in summary_lines[1:]:
        _append_line(
            parts,
            line,
            style=summary_style,
            first_prefix="  ",
            continuation_prefix="  ",
            terminal_width=terminal_width,
            measure_width=measure_width,
        )

    for detail in view.details:
        detail_style = (
            PREVIEW_MORE_STYLE
            if detail.state == "more"
            else ERROR_PREVIEW_MESSAGE_STYLE
        )
        detail_lines = _content_lines(detail.text)
        if not detail_lines:
            continue
        prefix, continuation, first = _detail_prefix(detail_lines[0])
        _append_line(
            parts,
            first,
            style=detail_style,
            first_prefix=prefix,
            continuation_prefix=continuation,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
        for line in detail_lines[1:]:
            _append_line(
                parts,
                line,
                style=detail_style,
                first_prefix=continuation,
                continuation_prefix=continuation,
                terminal_width=terminal_width,
                measure_width=measure_width,
            )

    spans = tuple(parts)
    return StyledBlock(
        plain_text="".join(part.text for part in spans),
        spans=spans,
        preserve_spans=True,
    )


def _append_line(
    parts: list[TextSpan],
    text: str,
    *,
    style: TextStyle,
    first_prefix: str,
    continuation_prefix: str,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None,
) -> None:
    """向 MCP 状态追加一条带树形悬挂缩进的逻辑行。"""
    parts.append(TextSpan("\n"))
    parts.extend(layout_styled_line(
        [TextSpan(text, style)],
        first_prefix=TextSpan(first_prefix, style),
        continuation_prefix=TextSpan(continuation_prefix, style),
        terminal_width=terminal_width,
        measure_width=measure_width,
    ))


def _content_lines(value: typing.Any) -> list[str]:
    """清理 MCP 展示文本并移除边界空白行。"""
    lines = sanitize_terminal_text(value).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _detail_prefix(text: str) -> tuple[str, str, str]:
    """拆分 MCP 详情中的树形前缀并返回对应续行前缀。"""
    if text.startswith("  ├ "):
        return "  ├ ", "  │ ", text[4:]
    if text.startswith("  └ "):
        return "  └ ", "    ", text[4:]
    return "  └ ", "    ", text


if __name__ == '__main__':
    pass
