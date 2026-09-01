# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from agent.application.views import (
    BatchCompletedView,
    BatchStartView,
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from ..styles import (
    ACTION_TOOL_STYLE,
    ERROR_DOT_STYLE,
    PREVIEW_STYLE,
    PREVIEW_TEXT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)
from frontends.terminal.text import sanitize_terminal_line
from frontends.terminal.text_layout import (
    clip_display_text,
    text_display_width
)

BATCH_DOT_STYLE = TextStyle(foreground="#F59E0B", bold=True)

_BATCH_PREVIEW_CALLS        = 4
_BATCH_PREVIEW_ARGUMENTS    = 3
_BATCH_RESULT_PREVIEW_LINES = 5
_BATCH_PREVIEW_WIDTH        = 120


def render_batch_start_view(
    view: BatchStartView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> StyledBlock:
    """把并行工具开始视图转换为中立展示块。"""
    title = _display_line(
        "• Parallel tools",
        prefix="",
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    lines = [title]

    spans = [
        TextSpan(title[:1], BATCH_DOT_STYLE),
        TextSpan(title[1:], TITLE_STYLE),
    ]

    calls         = view.calls[:_BATCH_PREVIEW_CALLS]
    omitted_calls = max(0, len(view.calls) - len(calls))
    row_count     = len(calls) + (1 if omitted_calls else 0)

    for index, call in enumerate(calls):

        is_last       = index == row_count - 1
        branch        = "└" if is_last else "├"
        detail_prefix = "    " if is_last else "  │ "
        call_prefix   = f"  {branch} "

        call_name = _display_line(
            call.name,
            prefix=call_prefix,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
        lines.append(f"{call_prefix}{call_name}")

        spans.extend((
            TextSpan("\n"),
            TextSpan(f"  {branch}", PREVIEW_STYLE),
            TextSpan(" ", PREVIEW_STYLE),
            TextSpan(call_name, ACTION_TOOL_STYLE),
        ))

        for line_index, line in enumerate(_argument_lines(call.arguments)):
            marker = "└ " if line_index == 0 else "  "
            prefix = f"{detail_prefix}{marker}"
            display_line = _display_line(
                line,
                prefix=prefix,
                terminal_width=terminal_width,
                measure_width=measure_width,
            )
            lines.append(f"{prefix}{display_line}")
            spans.extend((
                TextSpan("\n"),
                TextSpan(prefix, PREVIEW_STYLE),
                *_argument_spans(display_line),
            ))

    if omitted_calls:
        text = f"… +{omitted_calls} tools"
        lines.append(f"  └ {text}")
        spans.extend((
            TextSpan("\n"),
            TextSpan("  └ ", PREVIEW_STYLE),
            TextSpan(text, PREVIEW_TEXT_STYLE),
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
        detail_prefix = "    " if index == last_index else "  │ "
        lines.append(f"  {branch} {call.name}")

        arguments = json.dumps(
            call.arguments,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        ).splitlines()

        lines.extend(f"{detail_prefix}{line}" for line in arguments)

    return StyledBlock(plain_text="\n".join(lines))


def render_batch_completed_view(
    view: BatchCompletedView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None
) -> StyledBlock:
    """把并行工具完成视图转换为中立展示块。"""
    all_ok = all(result.ok for result in view.results)

    title = _display_line(
        "• Parallel tools completed",
        prefix="",
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    lines = [title]
    spans = [
        TextSpan(title[:1], SUCCESS_DOT_STYLE if all_ok else ERROR_DOT_STYLE),
        TextSpan(title[1:], TITLE_STYLE),
    ]

    results         = view.results[:_BATCH_PREVIEW_CALLS]
    omitted_results = max(0, len(view.results) - len(results))
    row_count       = len(results) + (1 if omitted_results else 0)

    for index, result in enumerate(results):

        is_last       = index == row_count - 1
        branch        = "└" if is_last else "├"
        detail_prefix = "    " if is_last else "  │ "
        status        = "ok" if result.ok else "failed"
        row_prefix    = f"  {branch} "
        status_suffix = f"  {status}"

        result_name = _display_line(
            result.name,
            prefix=f"{row_prefix}{status_suffix}",
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
        lines.append(f"{row_prefix}{result_name}{status_suffix}")

        spans.extend((
            TextSpan("\n"),
            TextSpan(f"  {branch}", PREVIEW_STYLE),
            TextSpan(" ", PREVIEW_STYLE),
            TextSpan(result_name, ACTION_TOOL_STYLE),
            TextSpan("  ", PREVIEW_STYLE),
            TextSpan(status, SUCCESS_DOT_STYLE if result.ok else ERROR_DOT_STYLE),
        ))

        for line_index, line in enumerate(_result_preview_lines(result.text)):
            marker = "└ " if line_index == 0 else "  "
            prefix = f"{detail_prefix}{marker}"

            display_line = _display_line(
                line,
                prefix=prefix,
                terminal_width=terminal_width,
                measure_width=measure_width,
            )
            lines.append(f"{prefix}{display_line}")
            spans.extend((
                TextSpan("\n"),
                TextSpan(prefix, PREVIEW_STYLE),
                TextSpan(display_line, PREVIEW_TEXT_STYLE),
            ))

    if omitted_results:
        text = f"… +{omitted_results} tools"
        lines.append(f"  └ {text}")
        spans.extend((
            TextSpan("\n"),
            TextSpan("  └ ", PREVIEW_STYLE),
            TextSpan(text, PREVIEW_TEXT_STYLE),
        ))

    return StyledBlock(
        plain_text="\n".join(lines),
        spans=tuple(spans),
        preserve_spans=True,
    )


def render_batch_completed_transcript_view(
    view: BatchCompletedView,
) -> StyledBlock:
    """把并行工具完整结果转换为记录块。"""
    lines      = ["• Parallel tools completed"]
    last_index = len(view.results) - 1

    for index, result in enumerate(view.results):

        branch        = "└" if index == last_index else "├"
        detail_prefix = "    " if index == last_index else "  │ "
        status        = "ok" if result.ok else "failed"

        lines.append(f"  {branch} {result.name}  {status}")

        result_lines = _trim_outer_blank_lines(result.text)
        for line_index, line in enumerate(result_lines):
            marker = "└ " if line_index == 0 else "  "
            lines.append(f"{detail_prefix}{marker}{line}")

    return StyledBlock(plain_text="\n".join(lines))


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
        for key in sorted(arguments, key=lambda item: str(item))[
            :_BATCH_PREVIEW_ARGUMENTS
        ]
    ]

    if len(arguments) > _BATCH_PREVIEW_ARGUMENTS:
        lines.append(
            f"… +{len(arguments) - _BATCH_PREVIEW_ARGUMENTS} args"
        )

    return lines


def _result_preview_lines(value: typing.Any) -> list[str]:
    """把并行工具结果压缩为有界多行预览。"""
    source = _trim_outer_blank_lines(value)
    if not source:
        return []

    lines = [
        _short_text(line, _BATCH_PREVIEW_WIDTH)
        for line in source[:_BATCH_RESULT_PREVIEW_LINES]
    ]

    omitted = len(source) - len(lines)
    if omitted:
        lines.append(f"… +{omitted} lines")

    return lines


def _argument_preview(value: typing.Any) -> str:
    """把参数值转换为单行预览文本。"""
    if isinstance(value, str):
        return repr(_short_text(value))
    if value is None:
        return "None"
    if isinstance(value, (int, float, bool)):
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


def _trim_outer_blank_lines(value: typing.Any) -> list[str]:
    """移除预览边界的空白行，同时保留正文内部空行。"""
    lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _display_line(
    value: typing.Any,
    *,
    prefix: str,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> str:
    """按树形前缀后的可用宽度生成单行展示文本。"""
    width_of = measure_width or text_display_width
    text     = sanitize_terminal_line(value, measure_width=width_of)

    if not isinstance(terminal_width, int) or terminal_width <= 0:
        return text

    return clip_display_text(
        text,
        width=max(0, terminal_width - width_of(prefix)),
        measure_width=width_of,
    )


if __name__ == '__main__':
    pass
