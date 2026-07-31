# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from ..models import (
    BatchCompletedView,
    BatchStartView,
    StyledBlock,
    TextSpan,
    TextStyle
)
from ..styles import (
    ACTION_TOOL_STYLE,
    ERROR_DOT_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)

BATCH_DOT_STYLE = TextStyle(foreground="#F59E0B", bold=True)


def render_batch_start_view(view: BatchStartView) -> StyledBlock:
    """把并行工具开始视图转换为中立展示块。"""
    lines = ["• Parallel tools"]
    spans = [
        TextSpan("•", BATCH_DOT_STYLE),
        TextSpan(" Parallel tools", TITLE_STYLE),
    ]
    last_index = len(view.calls) - 1

    for index, call in enumerate(view.calls):
        branch = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "
        lines.append(f"{branch} {call.name}")
        spans.extend((
            TextSpan("\n"),
            TextSpan(branch, PREVIEW_STYLE),
            TextSpan(" ", PREVIEW_STYLE),
            TextSpan(call.name, ACTION_TOOL_STYLE),
        ))
        for line in _argument_lines(call.arguments):
            lines.append(f"{detail_prefix}└ {line}")
            spans.extend((
                TextSpan("\n"),
                TextSpan(f"{detail_prefix}└ ", PREVIEW_STYLE),
                *_argument_spans(line),
            ))

    return StyledBlock(
        plain_text="\n".join(lines),
        spans=tuple(spans),
        preserve_spans=True,
    )


def render_batch_start_transcript_view(view: BatchStartView) -> StyledBlock:
    """把并行工具启动信息转换为完整记录块。"""
    lines      = ["• Parallel tools"]
    last_index = len(view.calls) - 1

    for index, call in enumerate(view.calls):
        branch        = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "
        lines.append(f"{branch} {call.name}")

        arguments = json.dumps(
            call.arguments,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        ).splitlines()

        lines.extend(f"{detail_prefix}{line}" for line in arguments)

    return StyledBlock(plain_text="\n".join(lines))


def render_batch_completed_view(view: BatchCompletedView) -> StyledBlock:
    """把并行工具完成视图转换为中立展示块。"""
    all_ok = all(result.ok for result in view.results)

    lines = ["• Parallel tools completed"]
    spans = [
        TextSpan("•", SUCCESS_DOT_STYLE if all_ok else ERROR_DOT_STYLE),
        TextSpan(" Parallel tools completed", TITLE_STYLE),
    ]

    last_index = len(view.results) - 1

    for index, result in enumerate(view.results):
        branch        = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "
        status        = "ok" if result.ok else "failed"

        lines.append(f"{branch} {result.name}  {status}")

        spans.extend((
            TextSpan("\n"),
            TextSpan(branch, PREVIEW_STYLE),
            TextSpan(" ", PREVIEW_STYLE),
            TextSpan(result.name, ACTION_TOOL_STYLE),
            TextSpan("  ", PREVIEW_STYLE),
            TextSpan(status, SUCCESS_DOT_STYLE if result.ok else ERROR_DOT_STYLE),
        ))

        if result.text:
            lines.append(f"{detail_prefix}└ {result.text}")
            spans.extend((
                TextSpan("\n"),
                TextSpan(f"{detail_prefix}└ ", PREVIEW_STYLE),
                TextSpan(result.text, PREVIEW_TEXT_STYLE),
            ))

    return StyledBlock(
        plain_text="\n".join(lines),
        spans=tuple(spans),
        preserve_spans=True,
    )


def _argument_spans(line: str) -> list[TextSpan]:
    """把参数行拆成中立样式片段。"""
    key, sep, value = str(line or "").partition("=")
    if not sep:
        return [TextSpan(line, PREVIEW_TEXT_STYLE)]
    return [
        TextSpan(key, PREVIEW_STYLE),
        TextSpan(sep, PREVIEW_STYLE),
        TextSpan(value, PREVIEW_TEXT_STYLE),
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


if __name__ == '__main__':
    pass
