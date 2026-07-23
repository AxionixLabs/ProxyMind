# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.models import TextSpan, TextStyle
from mind_app.presentation.styles import PREVIEW_STYLE
from mind_app.presentation.text_layout import (
    text_display_width,
    wrap_styled_line
)


def wrap_title_parts(
    parts: list[TextSpan],
    *,
    terminal_width: int,
    continuation_prefix: str,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
    measure_width: typing.Callable[[str], int] | None = None,
) -> list[TextSpan]:
    """按终端宽度换行标题片段，并保留续行左侧轨迹线。"""
    return wrap_styled_line(
        parts,
        terminal_width=max(16, int(terminal_width or 0)),
        continuation_prefix=part(continuation_prefix, PREVIEW_STYLE),
        measure_width=measure_width,
    )


def shell_title_needs_wrap(
    title: str,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> bool:
    """判断 Ran 标题是否需要主动换行并补续行轨迹线。"""
    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return False

    text = str(title or "")
    width_of = measure_width or text_display_width
    return text.startswith("• Ran ") and width_of(text) > terminal_width


if __name__ == '__main__':
    pass
