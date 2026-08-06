# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from mind_app.presentation.models import (
    TextSpan,
    TextStyle
)
from ..command_parts import render_command_parts
from mind_app.presentation.styles import (
    ACTION_EDIT_STYLE,
    ACTION_RUN_STYLE,
    ACTION_TOOL_CALLING_STYLE,
    ACTION_TOOL_INVOKED_STYLE,
    ACTION_TOOL_STYLE,
    DELTA_ADD_STYLE,
    DELTA_REMOVE_STYLE,
    ERROR_DOT_STYLE,
    SUCCESS_DOT_STYLE,
    TOOL_CALLING_DOT_STYLE,
    TITLE_STYLE
)


def title_parts(
    title: str,
    *,
    ok: bool | None,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
) -> list[TextSpan]:
    """把标题里的行数增删摘要拆成可独立着色的片段。"""
    base_style = TITLE_STYLE

    dot_style = (
        TOOL_CALLING_DOT_STYLE
        if ok is None
        else SUCCESS_DOT_STYLE if ok else ERROR_DOT_STYLE
    )

    body = title

    parts: list[TextSpan] = []
    if body.startswith("•"):
        parts.append(part("•", dot_style))
        body = body[1:]

    match = re.search(r"\(\+(\d+) -(\d+)\)", title)
    if not match:
        if body:
            parts.extend(_styled_action_body_parts(body, base_style=base_style, ok=ok, part=part))
        return parts

    start, end = match.span()

    if title.startswith("•"):
        start = max(0, start - 1)
    end = max(0, end - 1)

    add_count, remove_count = match.groups()

    if start:
        parts.extend(_styled_action_body_parts(body[:start], base_style=base_style, ok=ok, part=part))

    parts.extend([
        part("(", base_style),
        part(f"+{add_count}", DELTA_ADD_STYLE),
        part(" ", base_style),
        part(f"-{remove_count}", DELTA_REMOVE_STYLE),
        part(")", base_style),
    ])
    if end < len(body):
        parts.extend(_styled_action_body_parts(body[end:], base_style=base_style, ok=ok, part=part))

    return parts


def _styled_action_body_parts(
    body: str,
    *,
    base_style: TextStyle,
    ok: bool | None,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
) -> list[TextSpan]:
    """把标题动作词拆出来，参数仍保留常规标题色。"""
    if not body:
        return []

    action_style = _action_style_for_body(body)

    if not action_style:
        return _plain_body_parts(body, base_style=base_style, ok=ok, part=part)

    leading_len = len(body) - len(body.lstrip(" "))
    leading     = body[:leading_len]
    rest        = body[leading_len:]

    action, tail = _split_action(rest)

    parts: list[TextSpan] = []

    if leading:
        parts.append(part(leading, base_style))
    if action:
        parts.append(part(action, action_style))
    if action in {"Ran", "Started", "Running"}:
        parts.extend(_command_tail_parts(tail, base_style=base_style, ok=ok, part=part))
        return parts
    if tail:
        parts.extend(_plain_body_parts(tail, base_style=base_style, ok=ok, part=part))

    return parts


def _command_tail_parts(
    body: str,
    *,
    base_style: TextStyle,
    ok: bool | None,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
) -> list[TextSpan]:
    """把动作词后面的命令拆成独立颜色。"""
    if not body:
        return []

    command_body = body
    _ = ok

    leading_len = len(command_body) - len(command_body.lstrip(" "))
    leading     = command_body[:leading_len]
    command     = command_body[leading_len:]

    parts: list[TextSpan] = []
    if leading:
        parts.append(part(leading, base_style))
    if command:
        parts.extend(render_command_parts(command))

    return parts


def _plain_body_parts(
    body: str,
    *,
    base_style: TextStyle,
    ok: bool | None,
    part: typing.Callable[[str, TextStyle | None], TextSpan],
) -> list[TextSpan]:
    """标题正文保持原文本，失败状态由状态点表达。"""
    _ = ok
    return [part(body, base_style)]


def _split_action(body: str) -> tuple[str, str]:
    """拆分标题动作前缀和剩余文本。"""
    for action in (
        "Function Calling",
        "Function Invoked",
        "Writing stdin",
        "Wrote stdin",
    ):
        if body == action:
            return action, ""
        if body.startswith(f"{action} "):
            return action, body[len(action):]

    action, sep, tail = body.partition(" ")
    return action, f"{sep}{tail}" if sep or tail else ""


def _action_style_for_body(
    body: str
) -> TextStyle | None:
    """返回标题动作前缀的弱分类颜色。"""
    text  = body.lstrip()

    first, _tail = _split_action(text)

    if first in {"Added", "Applying", "Edited", "Deleted", "Patch"}:
        return ACTION_EDIT_STYLE
    if first in {"Ran", "Running", "Started"}:
        return ACTION_RUN_STYLE
    if first == "Function Calling":
        return ACTION_TOOL_CALLING_STYLE
    if first == "Function Invoked":
        return ACTION_TOOL_INVOKED_STYLE
    if first in {"Resetting", "Tool", "Writing stdin", "Wrote stdin"}:
        return ACTION_TOOL_STYLE

    return None


if __name__ == '__main__':
    pass
