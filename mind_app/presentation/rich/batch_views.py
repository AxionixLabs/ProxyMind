# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.stream_events.tool_traces.common import (
    ACTION_TOOL_STYLE,
    ERROR_DOT_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)
from ..models import (
    BatchCompletedView,
    BatchStartView
)
from .models import (
    DisplayPart,
    RenderedBlock
)

BATCH_DOT_STYLE = f"bold #F59E0B"


def render_batch_start_view(view: BatchStartView) -> RenderedBlock:
    """把并行工具开始视图转换为当前终端展示。"""
    lines      = ["• Parallel tools"]
    parts      = [
        _part("•", BATCH_DOT_STYLE),
        _part(" Parallel tools", TITLE_STYLE),
    ]
    last_index = len(view.calls) - 1

    for index, call in enumerate(view.calls):
        branch        = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "

        lines.append(f"{branch} {call.name}")
        parts.extend([
            _part("\n", None),
            _part(branch, PREVIEW_STYLE),
            _part(" ", PREVIEW_STYLE),
            _part(call.name, ACTION_TOOL_STYLE),
        ])

        for line in _argument_lines(call.arguments):
            lines.append(f"{detail_prefix}└ {line}")
            parts.extend([
                _part("\n", None),
                _part(f"{detail_prefix}└ ", PREVIEW_STYLE),
                *_argument_parts(line),
            ])

    return RenderedBlock(
        text="\n".join(lines),
        display_parts=tuple(parts),
        preserve_display_parts=True,
    )


def render_batch_completed_view(view: BatchCompletedView) -> RenderedBlock:
    """把并行工具完成视图转换为当前终端展示。"""
    all_ok = all(result.ok for result in view.results)
    lines  = ["• Parallel tools completed"]
    parts  = [
        _part("•", SUCCESS_DOT_STYLE if all_ok else ERROR_DOT_STYLE),
        _part(" Parallel tools completed", TITLE_STYLE),
    ]
    last_index = len(view.results) - 1

    for index, result in enumerate(view.results):
        branch        = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "
        status        = "ok" if result.ok else "failed"

        lines.append(f"{branch} {result.name}  {status}")
        parts.extend([
            _part("\n", None),
            _part(branch, PREVIEW_STYLE),
            _part(" ", PREVIEW_STYLE),
            _part(result.name, ACTION_TOOL_STYLE),
            _part("  ", PREVIEW_STYLE),
            _part(status, SUCCESS_DOT_STYLE if result.ok else ERROR_DOT_STYLE),
        ])

        if result.text:
            lines.append(f"{detail_prefix}└ {result.text}")
            parts.extend([
                _part("\n", None),
                _part(f"{detail_prefix}└ ", PREVIEW_STYLE),
                _part(result.text, PREVIEW_TEXT_STYLE),
            ])

    return RenderedBlock(
        text="\n".join(lines),
        display_parts=tuple(parts),
        preserve_display_parts=True,
    )


def _argument_parts(line: str) -> list[DisplayPart]:
    """把参数行拆成当前终端使用的样式片段。"""
    key, sep, value = str(line or "").partition("=")
    if not sep:
        return [_part(line, PREVIEW_TEXT_STYLE)]

    return [
        _part(key, PREVIEW_STYLE),
        _part(sep, PREVIEW_STYLE),
        _part(value, PREVIEW_TEXT_STYLE),
    ]


def _argument_lines(arguments: dict[str, typing.Any]) -> list[str]:
    """生成紧凑的参数预览行。"""
    if not isinstance(arguments, dict) or not arguments:
        return ["no args"]

    lines = [
        f"{key}={_argument_preview(arguments.get(key))}"
        for key in sorted(arguments, key=lambda item: str(item))[:6]
    ]
    if len(arguments) > 6:
        lines.append(f"… +{len(arguments) - 6} args")
    return lines


def _argument_preview(value: typing.Any) -> str:
    """把参数值转换为单行预览文本。"""
    if isinstance(value, str):
        return repr(_short_text(value))
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    if isinstance(value, dict):
        return f"<dict:{len(value)}>"
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__}:{len(value)}>"
    return _short_text(value)


def _short_text(value: typing.Any, limit: int = 120) -> str:
    """压缩单行文本。"""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


def _part(text: str, style: str | None) -> DisplayPart:
    """构造带样式的终端文本片段。"""
    return {"text": text, "style": style}


if __name__ == '__main__':
    pass
