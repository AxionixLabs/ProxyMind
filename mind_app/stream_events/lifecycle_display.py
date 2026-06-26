# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

LIFECYCLE_DOT_STYLE   = "bold #8A929C"
LIFECYCLE_TITLE_STYLE = "bold #C9D3DE"


def render_lifecycle_display_parts(
    title: str
) -> list[dict[str, typing.Optional[str]]]:
    """把生命周期 display 标题转换为专用显示片段。"""
    text = str(title or "")
    if not text:
        return []

    if not text.startswith("•"):
        return [{"text": text, "style": LIFECYCLE_TITLE_STYLE}]

    parts: list[dict[str, typing.Optional[str]]] = [
        {"text": "•", "style": LIFECYCLE_DOT_STYLE}
    ]
    body = text[1:]
    if body:
        parts.append({"text": body, "style": LIFECYCLE_TITLE_STYLE})
    return parts


if __name__ == '__main__':
    pass
