# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from prompt_toolkit.utils import get_cwidth
from mind_app.presentation.terminal_text import (
    TerminalTextFilter,
    sanitize_terminal_hyperlink
)
from ..contracts.text import (
    FormattedText,
    FragmentBlock
)
from .fragments import ZERO_WIDTH_ESCAPE_STYLE

__all__ = [
    "OSC8_PREFIX",
    "OSC8_SUFFIX",
    "sanitize_formatted_text",
    "sanitize_fragment_block",
]

OSC8_PREFIX = "\x1b]8;;"
OSC8_SUFFIX = "\x1b\\"

_UNSAFE_TERMINAL_TEXT = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def sanitize_formatted_text(
    parts: typing.Iterable[tuple[str, str]]
) -> FormattedText:
    """清理格式化文本中的终端控制序列并保留样式边界。"""
    source = list(parts)
    if all(
        ZERO_WIDTH_ESCAPE_STYLE not in style
        and _UNSAFE_TERMINAL_TEXT.search(text) is None
        for style, text in source
    ):
        return source

    text_filter = TerminalTextFilter(measure_width=get_cwidth)

    out: FormattedText = []

    last_style: str = ""

    for style, text in source:
        if not text:
            out.append((style, text))
            continue
        if ZERO_WIDTH_ESCAPE_STYLE in style:
            escape = _sanitize_zero_width_escape(text)
            if escape:
                out.append((ZERO_WIDTH_ESCAPE_STYLE, escape))
            continue
        last_style = style
        _append_fragment(out, style, text_filter.feed(text))
    _append_fragment(out, last_style, text_filter.finish())
    return out


def sanitize_fragment_block(block: FragmentBlock) -> FragmentBlock:
    """返回只包含安全显示文本的片段块。"""
    fragments = tuple(sanitize_formatted_text(block.fragments))
    if fragments == block.fragments:
        return block

    return FragmentBlock(
        fragments,
        line_fill=block.line_fill,
        line_fills=block.line_fills,
    )


def _append_fragment(parts: FormattedText, style: str, text: str) -> None:
    """追加非空的安全片段并保留原有样式边界。"""
    if text:
        parts.append((style, text))


def _sanitize_zero_width_escape(text: str) -> str:
    """只保留内部支持的 OSC 8 开始和结束序列。"""
    value = str(text or "")
    if value == f"{OSC8_PREFIX}{OSC8_SUFFIX}":
        return value
    if not value.startswith(OSC8_PREFIX) or not value.endswith(OSC8_SUFFIX):
        return ""

    url      = value[len(OSC8_PREFIX):-len(OSC8_SUFFIX)]
    safe_url = sanitize_terminal_hyperlink(url)

    return f"{OSC8_PREFIX}{safe_url}{OSC8_SUFFIX}" if safe_url else ""


if __name__ == '__main__':
    pass
