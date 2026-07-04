# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.stream_ui import StreamUI
from mind_app.stream_events.tool_traces.common import (
    ACTION_TOOL_STYLE,
    ERROR_DOT_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)
from .batch import (
    BatchToolResult,
    ToolCallBatch
)

BATCH_DOT_STYLE = "bold #F59E0B"


def should_group_batch(batch: ToolCallBatch) -> bool:
    """判断 batch 是否需要聚合展示。"""
    return len(batch.calls) > 1 and not any(call.use_coding_trace for call in batch.calls)


async def show_tool_batch_start(
    stream_ui: StreamUI,
    batch: ToolCallBatch
) -> None:
    """展示一批工具调用的聚合开始块。"""
    if not should_group_batch(batch):
        return None

    for call in batch.calls:
        stream_ui.record_tool_arguments(
            call.name,
            call.arguments,
            call_id=str(call.event.get("call_id") or "")
        )

    lines      = ["• Parallel tools"]
    last_index = len(batch.calls) - 1

    for index, call in enumerate(batch.calls):
        branch        = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "

        lines.append(f"{branch} {call.name}")

        for line in _argument_lines(call.arguments):
            lines.append(f"{detail_prefix}└ {line}")

    text = "\n".join(lines)
    await stream_ui.feed(
        text,
        display=StreamUI.BLOCK,
        display_parts=_start_parts(batch),
        preserve_display_parts=True
    )


async def show_tool_batch_completed(
    stream_ui: StreamUI,
    results: list[BatchToolResult]
) -> None:
    """展示一批工具调用的聚合完成块。"""
    if len(results) <= 1:
        return None

    lines = ["• Parallel tools completed"]
    last_index = len(results) - 1

    for index, result in enumerate(results):
        branch        = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "
        status        = "ok" if result.ok else "failed"

        lines.append(f"{branch} {result.name}  {status}")
        if result.text:
            lines.append(f"{detail_prefix}└ {result.text}")

    text = "\n".join(lines)
    await stream_ui.feed(
        text,
        display=StreamUI.BLOCK,
        display_parts=_completed_parts(results),
        preserve_display_parts=True
    )


def _part(text: str, style: str | None) -> dict[str, typing.Optional[str]]:
    """创建一段带样式的显示片段。"""
    return {"text": text, "style": style}


def _start_parts(batch: ToolCallBatch) -> list[dict[str, typing.Optional[str]]]:
    """生成 batch start 的彩色片段。"""
    parts: list[dict[str, typing.Optional[str]]] = [
        _part("•", BATCH_DOT_STYLE),
        _part(" Parallel tools", TITLE_STYLE)
    ]
    last_index = len(batch.calls) - 1

    for index, call in enumerate(batch.calls):
        branch        = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "

        parts.extend([
            _part("\n", None),
            _part(branch, PREVIEW_STYLE),
            _part(" ", PREVIEW_STYLE),
            _part(call.name, ACTION_TOOL_STYLE),
        ])

        for line in _argument_lines(call.arguments):
            parts.extend([
                _part("\n", None),
                _part(f"{detail_prefix}└ ", PREVIEW_STYLE),
                *_argument_parts(line)
            ])

    return parts


def _completed_parts(results: list[BatchToolResult]) -> list[dict[str, typing.Optional[str]]]:
    """生成 batch completed 的彩色片段。"""
    all_ok = all(result.ok for result in results)

    parts: list[dict[str, typing.Optional[str]]] = [
        _part("•", SUCCESS_DOT_STYLE if all_ok else ERROR_DOT_STYLE),
        _part(" Parallel tools completed", TITLE_STYLE)
    ]
    last_index = len(results) - 1

    for index, result in enumerate(results):
        branch        = "└" if index == last_index else "├"
        detail_prefix = "   " if index == last_index else "│  "
        status        = "ok" if result.ok else "failed"

        parts.extend([
            _part("\n", None),
            _part(branch, PREVIEW_STYLE),
            _part(" ", PREVIEW_STYLE),
            _part(result.name, ACTION_TOOL_STYLE),
            _part("  ", PREVIEW_STYLE),
            _part(status, SUCCESS_DOT_STYLE if result.ok else ERROR_DOT_STYLE),
        ])
        if result.text:
            parts.extend([
                _part("\n", None),
                _part(f"{detail_prefix}└ ", PREVIEW_STYLE),
                _part(result.text, PREVIEW_TEXT_STYLE)
            ])

    return parts


def _argument_parts(line: str) -> list[dict[str, typing.Optional[str]]]:
    """把参数行拆成 key/value 颜色片段。"""
    key, sep, value = str(line or "").partition("=")
    if not sep:
        return [_part(line, PREVIEW_TEXT_STYLE)]

    return [
        _part(key, PREVIEW_STYLE),
        _part(sep, PREVIEW_STYLE),
        _part(value, PREVIEW_TEXT_STYLE)
    ]


def _argument_lines(arguments: dict[str, typing.Any]) -> list[str]:
    """生成简短参数行。"""
    if not isinstance(arguments, dict) or not arguments:
        return ["no args"]

    lines: list[str] = []
    for key in sorted(arguments, key=lambda item: str(item))[:6]:
        lines.append(f"{key}={_argument_preview(arguments.get(key))}")
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
