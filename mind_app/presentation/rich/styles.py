# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from ..models import TextStyle


def rich_style(style: TextStyle) -> str | None:
    """把中立文本样式转换为 Rich 样式字符串。"""
    parts = [
        name
        for enabled, name in (
            (style.bold, "bold"),
            (style.dim, "dim"),
            (style.italic, "italic"),
            (style.underline, "underline"),
            (style.reverse, "reverse"),
            (style.strikethrough, "strike"),
        )
        if enabled
    ]

    if style.foreground:
        parts.append(style.foreground)
    if style.background:
        parts.extend(("on", style.background))

    return " ".join(parts) or None


if __name__ == '__main__':
    pass
