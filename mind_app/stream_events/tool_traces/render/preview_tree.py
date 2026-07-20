# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from mind_app.presentation.models import TextSpan, TextStyle

from ..command_parts import render_command_parts
from mind_app.presentation.styles import (
    ERROR_DOT_STYLE,
    ERROR_PREVIEW_HEAD_STYLE,
    ERROR_PREVIEW_TEXT_STYLE,
    PREVIEW_COUNT_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE
)


def tree_preview_line_parts(
    line: str,
    *,
    is_error_detail: bool,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
) -> tuple[list[TextSpan], str, bool | None] | None:
    """拆分树形预览行，并返回下一条 detail 是否应按错误渲染。"""
    omitted = re.match(r"^([├└]─ )(… \+)(\d+)( commands?)$", line)
    if omitted:
        prefix, head, count, tail = omitted.groups()
        return [
            part(prefix, PREVIEW_STYLE),
            part(head, PREVIEW_STYLE),
            part(count, PREVIEW_COUNT_STYLE),
            part(tail, PREVIEW_STYLE),
        ], "", False

    item = re.match(r"^([├└]─ )([✓✗]) (.+)$", line)
    if item:
        prefix, mark, label = item.groups()

        mark_style = SUCCESS_DOT_STYLE if mark == "✓" else ERROR_DOT_STYLE

        return [
            part(prefix, PREVIEW_STYLE),
            part(mark, mark_style),
            part(" ", PREVIEW_STYLE),
            *render_command_parts(label),
        ], "", mark == "✗"

    detail = re.match(r"^((?:│ {2}| {3})└─ )(.+)$", line)
    if detail:
        prefix, body = detail.groups()
        if is_error_detail:
            return [
                part(prefix, PREVIEW_STYLE),
                *error_summary_parts(body, part=part),
            ], "", False
        return [
            part(prefix, PREVIEW_STYLE),
            part(body, PREVIEW_TEXT_STYLE),
        ], "", False

    return None


def error_summary_parts(
    body: str,
    *,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
) -> list[TextSpan]:
    """渲染紧凑错误摘要。"""
    text = str(body or "")
    head = re.match(r"^([A-Za-z][A-Za-z0-9_. -]*:)(\s*)(.*)$", text)

    if head:
        return [
            part(head.group(1), ERROR_PREVIEW_HEAD_STYLE),
            part(head.group(2), PREVIEW_STYLE),
            part(head.group(3), ERROR_PREVIEW_TEXT_STYLE)
        ]

    return [part(text, ERROR_PREVIEW_TEXT_STYLE)]


if __name__ == '__main__':
    pass
