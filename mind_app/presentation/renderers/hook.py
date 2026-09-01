# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.formatting import format_duration_ms
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from agent.application.views import (
    HookOutputView,
    HookRunView,
)
from mind_app.presentation.styles import (
    ERROR_DOT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)
from frontends.terminal.text import (
    sanitize_terminal_line,
    sanitize_terminal_text
)
from frontends.terminal.text_layout import layout_styled_line

_MUTED_STYLE   = TextStyle(foreground="#7F8C9A", dim=True)
_WARNING_STYLE = TextStyle(foreground="#FFD75F", bold=True)


def render_hook_run_view(
    view: HookRunView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把 Hook 生命周期视图转换为紧凑文本块。"""
    if view.phase == "started":
        return _render_started(
            view,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
    return _render_completed(
        view,
        terminal_width=terminal_width,
        measure_width=measure_width,
    )


def _render_started(
    view: HookRunView,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> StyledBlock:
    """生成 Hook 开始展示块。"""
    title         = f"Running {sanitize_terminal_line(view.event)} hook"
    message_lines = _content_lines(view.status_message)
    title_parts   = [TextSpan(f" {title}", TITLE_STYLE)]

    if message_lines:
        title_parts.extend((
            TextSpan(": ", TITLE_STYLE),
            TextSpan(message_lines[0], _MUTED_STYLE),
        ))

    spans = layout_styled_line(
        title_parts,
        first_prefix=TextSpan("•", _MUTED_STYLE),
        continuation_prefix=TextSpan("  ", _MUTED_STYLE),
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    for line in message_lines[1:]:
        _append_layout_line(
            spans,
            [TextSpan(line, _MUTED_STYLE)],
            first_prefix=TextSpan("  ", _MUTED_STYLE),
            continuation_prefix=TextSpan("  ", _MUTED_STYLE),
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
    return _block(spans)


def _render_completed(
    view: HookRunView,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> StyledBlock:
    """生成 Hook 完成展示块。"""
    title         = f"Ran {sanitize_terminal_line(view.event)} hook"
    message_lines = _content_lines(view.status_message)

    bullet_style = (
        SUCCESS_DOT_STYLE
        if view.status == "completed" and not _has_warning(view)
        else _WARNING_STYLE
        if view.status == "completed"
        else ERROR_DOT_STYLE
    )
    title_parts = [TextSpan(f" {title}", TITLE_STYLE)]
    if message_lines:
        title_parts.extend((
            TextSpan(": ", TITLE_STYLE),
            TextSpan(message_lines[0], _MUTED_STYLE),
        ))
    spans = layout_styled_line(
        title_parts,
        first_prefix=TextSpan("•", bullet_style),
        continuation_prefix=TextSpan("  ", TITLE_STYLE),
        terminal_width=terminal_width,
        measure_width=measure_width,
    )
    for line in message_lines[1:]:
        _append_layout_line(
            spans,
            [TextSpan(line, _MUTED_STYLE)],
            first_prefix=TextSpan("  ", _MUTED_STYLE),
            continuation_prefix=TextSpan("  ", _MUTED_STYLE),
            terminal_width=terminal_width,
            measure_width=measure_width,
        )

    status_parts = [TextSpan(view.status, _MUTED_STYLE)]
    if view.duration_ms is not None:
        status_parts.append(TextSpan(
            f" · {format_duration_ms(view.duration_ms)}",
            _MUTED_STYLE,
        ))
    _append_layout_line(
        spans,
        status_parts,
        first_prefix=TextSpan("  └ ", _MUTED_STYLE),
        continuation_prefix=TextSpan("    ", _MUTED_STYLE),
        terminal_width=terminal_width,
        measure_width=measure_width,
    )

    for entry in view.entries:
        _append_entry(
            spans,
            entry,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )

    return _block(spans)


def _append_entry(
    spans: list[TextSpan],
    entry: HookOutputView,
    *,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None,
) -> None:
    """把 Hook 输出条目转换为树形缩进文本。"""
    prefix = {
        "warning": "warning: ",
        "stop": "stop: ",
        "feedback": "feedback: ",
        "context": "hook context: ",
        "error": "error: ",
    }.get(entry.kind, f"{entry.kind}: ")

    source = _content_lines(entry.text)
    if not source:
        return None

    for index, line in enumerate(source):
        text = f"{prefix}{line}" if index == 0 else line
        _append_layout_line(
            spans,
            [TextSpan(text)],
            first_prefix=TextSpan("    "),
            continuation_prefix=TextSpan("    "),
            terminal_width=terminal_width,
            measure_width=measure_width,
        )


def _has_warning(view: HookRunView) -> bool:
    """判断完成结果是否包含告警条目。"""
    return any(entry.kind == "warning" for entry in view.entries)


def _block(spans: list[TextSpan]) -> StyledBlock:
    """由样式片段构建展示块。"""
    return StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=tuple(spans),
        direct=True,
    )


def _append_layout_line(
    spans: list[TextSpan],
    parts: list[TextSpan],
    *,
    first_prefix: TextSpan,
    continuation_prefix: TextSpan,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None,
) -> None:
    """向 Hook 展示块追加一条带悬挂缩进的逻辑行。"""
    spans.append(TextSpan("\n"))
    spans.extend(layout_styled_line(
        parts,
        first_prefix=first_prefix,
        continuation_prefix=continuation_prefix,
        terminal_width=terminal_width,
        measure_width=measure_width,
    ))


def _content_lines(value: typing.Any) -> list[str]:
    """清理 Hook 文本并移除边界空白行。"""
    lines = sanitize_terminal_text(value).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


if __name__ == '__main__':
    pass
