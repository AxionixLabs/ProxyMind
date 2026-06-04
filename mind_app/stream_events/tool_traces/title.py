# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from .common import (
    ACTION_EDIT_STYLE,
    ACTION_GIT_STYLE,
    ACTION_READ_STYLE,
    ACTION_RUN_STYLE,
    COUNT_UNIT_STYLE,
    COUNT_VALUE_STYLE,
    DELTA_ADD_STYLE,
    DELTA_REMOVE_STYLE,
    ERROR_DOT_STYLE,
    ERROR_STYLE,
    PREVIEW_COUNT_STYLE,
    PREVIEW_LINE_STYLE,
    PREVIEW_MORE_STYLE,
    PREVIEW_PATH_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE,
    TracePreview,
    _preview_text
)


def render_tool_trace_parts(
    title: str,
    *,
    preview: typing.Optional[typing.Union[str, TracePreview]] = None,
    ok: bool = True
) -> list[dict[str, typing.Optional[str]]]:
    """把轨迹标题和预览内容转换为带样式的文本片段。"""
    parts = _title_parts(title, ok=ok)

    preview_text = preview.screen if isinstance(preview, TracePreview) else _preview_text(preview)
    if preview_text:
        parts.extend([
            {"text": "\n", "style": None},
            {"text": "└ ", "style": PREVIEW_STYLE},
            *_preview_parts(preview_text),
        ])

    return parts


def _title_parts(
    title: str,
    *,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """把标题里的行数增删摘要拆成可独立着色的片段。"""
    base_style = TITLE_STYLE if ok else ERROR_STYLE
    dot_style  = SUCCESS_DOT_STYLE if ok else ERROR_DOT_STYLE
    body       = title

    parts: list[dict[str, typing.Optional[str]]] = []
    if body.startswith("•"):
        parts.append({"text": "•", "style": dot_style})
        body = body[1:]

    match = re.search(r"\(\+(\d+) -(\d+)\)", title)
    if not match:
        count_parts = _title_count_parts(body, base_style=base_style, ok=ok)
        if count_parts:
            parts.extend(count_parts)
            return parts
        if body:
            parts.extend(_styled_action_body_parts(body, base_style=base_style, ok=ok))
        return parts

    start, end = match.span()

    if title.startswith("•"):
        start = max(0, start - 1)
    end = max(0, end - 1)

    add_count, remove_count = match.groups()

    if start:
        parts.extend(_styled_action_body_parts(body[:start], base_style=base_style, ok=ok))

    parts.extend([
        {"text": "(", "style": base_style},
        {"text": f"+{add_count}", "style": DELTA_ADD_STYLE},
        {"text": " ", "style": base_style},
        {"text": f"-{remove_count}", "style": DELTA_REMOVE_STYLE},
        {"text": ")", "style": base_style},
    ])
    if end < len(body):
        parts.extend(_styled_action_body_parts(body[end:], base_style=base_style, ok=ok))

    return parts


def _title_count_parts(
    body: str,
    *,
    base_style: str,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """拆分标题里的计数摘要，如 (84 matches)。"""
    match = re.search(r"\((\d+) (items|matches|files|symbols|results|sessions)([^)]*)\)", body)
    if not match:
        return []

    start, end = match.span()

    count, unit, tail = match.groups()

    parts: list[dict[str, typing.Optional[str]]] = []

    if start:
        parts.extend(_styled_action_body_parts(body[:start], base_style=base_style, ok=ok))

    parts.extend([
        {"text": "(", "style": base_style},
        {"text": count, "style": COUNT_VALUE_STYLE if ok else base_style},
        {"text": " ", "style": base_style},
        {"text": unit, "style": COUNT_UNIT_STYLE if ok else base_style},
        {"text": tail, "style": base_style},
        {"text": ")", "style": base_style},
    ])

    if end < len(body):
        parts.extend(_styled_action_body_parts(body[end:], base_style=base_style, ok=ok))

    return parts


def _styled_action_body_parts(
    body: str,
    *,
    base_style: str,
    ok: bool
) -> list[dict[str, typing.Optional[str]]]:
    """把标题动作词拆出来，参数仍保留常规标题色。"""
    if not body:
        return []
    action_style = _action_style_for_body(body, ok=ok)
    if not action_style:
        return [{"text": body, "style": base_style}]

    leading_len = len(body) - len(body.lstrip(" "))
    leading     = body[:leading_len]
    rest        = body[leading_len:]

    action, sep, tail = rest.partition(" ")

    parts: list[dict[str, typing.Optional[str]]] = []
    if leading:
        parts.append({"text": leading, "style": base_style})
    if action:
        parts.append({"text": action, "style": action_style})
    if sep or tail:
        parts.append({"text": f"{sep}{tail}", "style": base_style})

    return parts


def _action_style_for_body(
    body: str,
    *,
    ok: bool
) -> str | None:
    """返回标题动作前缀的弱分类颜色。"""
    if not ok:
        return None

    text  = body.lstrip()
    first = text.split(" ", 1)[0] if text else ""

    if first in {"Git", "Change"}:
        return ACTION_GIT_STYLE
    if first in {"Read", "Listed", "Searched", "Root", "Skipping", "Skipped"}:
        return ACTION_READ_STYLE
    if first in {"Added", "Edited", "Created", "Deleted", "Copied", "Moved", "Patch"}:
        return ACTION_EDIT_STYLE
    if first in {"Ran", "Recorded", "Rolled", "Updated"}:
        return ACTION_RUN_STYLE

    return None


def _preview_parts(
    preview_text: str
) -> list[dict[str, typing.Optional[str]]]:
    """把预览摘要拆成路径、行号、内容和省略提示片段。"""
    lines = str(preview_text or "").split("\n")
    parts: list[dict[str, typing.Optional[str]]] = []
    for index, line in enumerate(lines):
        if index:
            parts.append({"text": "\n  ", "style": PREVIEW_STYLE})
        parts.extend(_preview_line_parts(line))
    return parts


def _preview_line_parts(
    line: str
) -> list[dict[str, typing.Optional[str]]]:
    """拆分单行预览摘要。"""
    more = re.match(r"^(… \+)(\d+)( lines)$", line)
    if more:
        return [
            {"text": more.group(1), "style": PREVIEW_MORE_STYLE},
            {"text": more.group(2), "style": PREVIEW_COUNT_STYLE},
            {"text": more.group(3), "style": PREVIEW_MORE_STYLE},
        ]

    location = re.match(r"^([^:\s][^:\n]*):(\d+)(.*)$", line)
    if location:
        return [
            {"text": location.group(1), "style": PREVIEW_PATH_STYLE},
            {"text": ":", "style": PREVIEW_STYLE},
            {"text": location.group(2), "style": PREVIEW_LINE_STYLE},
            {"text": location.group(3), "style": PREVIEW_TEXT_STYLE},
        ]

    return [{"text": line, "style": PREVIEW_TEXT_STYLE}]


if __name__ == '__main__':
    pass
