# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

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

MCP_STATUS_BODY_STYLE    = TextStyle(foreground="#DDE7EF")
MCP_STATUS_WARNING_STYLE = TextStyle(foreground="#F59E0B", bold=True)


def render_mcp_status_block(view: McpStatusView) -> StyledBlock:
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

    parts: list[TextSpan] = [
        TextSpan("■", marker_style),
        TextSpan(" ", MCP_STATUS_BODY_STYLE),
        TextSpan(summary, summary_style),
    ]
    for detail in view.details:
        detail_style = (
            PREVIEW_MORE_STYLE
            if detail.state == "more"
            else ERROR_PREVIEW_MESSAGE_STYLE
        )
        parts.extend((
            TextSpan("\n"),
            TextSpan(detail.text, detail_style),
        ))

    spans = tuple(parts)
    return StyledBlock(
        plain_text="".join(part.text for part in spans),
        spans=spans,
        preserve_spans=True,
    )


if __name__ == '__main__':
    pass
