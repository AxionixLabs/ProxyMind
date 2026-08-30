# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.presentation.models import TextSpan, TextStyle

LIFECYCLE_DOT_STYLE   = TextStyle(foreground="#8A929C", bold=True)
LIFECYCLE_TITLE_STYLE = TextStyle(foreground="#C9D3DE", bold=True)


def render_lifecycle_display_parts(
    title: str
) -> list[TextSpan]:
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
