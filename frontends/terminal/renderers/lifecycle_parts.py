# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports.presentation import TextSpan
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    semantic_text_style,
)

LIFECYCLE_DOT_STYLE = semantic_text_style(
    TerminalSemanticRole.SECONDARY,
    bold=True,
)
LIFECYCLE_TITLE_STYLE = semantic_text_style(
    TerminalSemanticRole.PRIMARY,
    bold=True,
)


def render_lifecycle_display_parts(title: str) -> list[TextSpan]:
    """把生命周期 display 标题转换为专用显示片段。"""
    text = str(title or "")
    if not text:
        return []

    if not text.startswith("•"):
        return [TextSpan(text, LIFECYCLE_TITLE_STYLE)]

    parts: list[TextSpan] = [
        TextSpan("•", LIFECYCLE_DOT_STYLE)
    ]
    body = text[1:]
    if body:
        parts.append(TextSpan(body, LIFECYCLE_TITLE_STYLE))
    return parts


if __name__ == '__main__':
    pass
