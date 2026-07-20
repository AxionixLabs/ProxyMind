# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from .models import FragmentBlock

MUTED_STYLE = TextStyle(foreground="#7F8C9A", dim=True)
ACCENT_STYLE = TextStyle(foreground="#AFC7D8", bold=True)
BRIGHT_STYLE = TextStyle(foreground="#F4F7FA", bold=True)
BODY_STYLE = TextStyle(foreground="#DDE7EF")
SUCCESS_STYLE = TextStyle(foreground="#5FD7AF", bold=True)
WARNING_STYLE = TextStyle(foreground="#FFB86B", bold=True)
FAILURE_STYLE = TextStyle(foreground="#FF6B6B", bold=True)


def prompt_style(style: TextStyle) -> str:
    """把中立文本样式转换为 prompt_toolkit 样式字符串。"""
    parts = [
        name
        for enabled, name in (
            (style.bold, "bold"),
            (style.dim, "dim"),
            (style.italic, "italic"),
            (style.underline, "underline"),
            (style.reverse, "reverse"),
        )
        if enabled
    ]
    if style.foreground:
        parts.append(f"fg:{style.foreground}")
    if style.background:
        parts.append(f"bg:{style.background}")
    return " ".join(parts)


def styled_block_fragments(
    block: StyledBlock,
    *,
    fallback_style: TextStyle | None = None,
) -> tuple[tuple[str, str], ...]:
    """把中立展示块转换为 prompt_toolkit 文本片段。"""
    spans = block.spans
    if not spans:
        style = prompt_style(fallback_style or TextStyle())
        return ((style, block.plain_text),) if block.plain_text else ()
    return tuple(
        (
            prompt_style(
                span.style
                if span.style != TextStyle() or fallback_style is None
                else fallback_style
            ),
            span.text,
        )
        for span in spans
        if span.text
    )


def fragment_block(*parts: str | TextSpan) -> FragmentBlock:
    """把有序纯文本或中立文本片段生成 TUI 块。"""
    spans = tuple(
        part if isinstance(part, TextSpan) else TextSpan(str(part))
        for part in parts
        if isinstance(part, TextSpan) or str(part)
    )
    block = StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=spans,
    )
    return FragmentBlock(styled_block_fragments(block))


def text_block(text: str, style: TextStyle = TextStyle()) -> FragmentBlock:
    """生成单样式 TUI 文本块。"""
    return fragment_block(TextSpan(str(text), style))


def query_block(text: str) -> FragmentBlock:
    """按普通 query 或命令类型生成用户输入块。"""
    value = str(text).strip()
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    command = value.startswith(("/", "!", "$", "\\"))
    fragments: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if index:
            fragments.append(("", "\n"))
        marker = "" if command else ("> " if index == 0 else "  ")
        if marker:
            fragments.append(("class:prompt.kicker", marker))
        fragments.append(("class:prompt", line))
    return FragmentBlock(tuple(fragments))


if __name__ == '__main__':
    pass
