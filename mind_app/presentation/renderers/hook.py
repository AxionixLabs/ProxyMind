# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.presentation.formatting import format_duration_ms
from mind_app.presentation.models import (
    HookOutputView,
    HookRunView,
    StyledBlock,
    TextSpan,
    TextStyle
)
from mind_app.presentation.styles import (
    ERROR_DOT_STYLE,
    SUCCESS_DOT_STYLE,
    TITLE_STYLE
)

_MUTED_STYLE   = TextStyle(foreground="#7F8C9A", dim=True)
_WARNING_STYLE = TextStyle(foreground="#FFD75F", bold=True)


def render_hook_run_view(view: HookRunView) -> StyledBlock:
    """把 Hook 生命周期视图转换为紧凑文本块。"""
    if view.phase == "started":
        return _render_started(view)
    return _render_completed(view)


def _render_started(view: HookRunView) -> StyledBlock:
    """生成 Hook 开始展示块。"""
    title = f"Running {view.event} hook"
    spans = [
        TextSpan("•", _MUTED_STYLE),
        TextSpan(f" {title}", TITLE_STYLE),
    ]
    if view.status_message:
        spans.extend((
            TextSpan(": ", TITLE_STYLE),
            TextSpan(view.status_message, _MUTED_STYLE),
        ))
    return _block(spans)


def _render_completed(view: HookRunView) -> StyledBlock:
    """生成 Hook 完成展示块。"""
    title = f"Ran {view.event} hook"
    if view.status_message:
        title = f"{title}: {view.status_message}"

    bullet_style = (
        SUCCESS_DOT_STYLE
        if view.status == "completed" and not _has_warning(view)
        else _WARNING_STYLE
        if view.status == "completed"
        else ERROR_DOT_STYLE
    )
    spans = [
        TextSpan("•", bullet_style),
        TextSpan(f" {title}\n", TITLE_STYLE),
        TextSpan(f"  └ {view.status}", _MUTED_STYLE),
    ]
    if view.duration_ms is not None:
        spans.append(TextSpan(
            f" · {format_duration_ms(view.duration_ms)}",
            _MUTED_STYLE,
        ))

    for entry in view.entries:
        spans.append(TextSpan("\n"))
        spans.extend(_entry_spans(entry))

    return _block(spans)


def _entry_spans(entry: HookOutputView) -> list[TextSpan]:
    """把 Hook 输出条目转换为树形缩进文本。"""
    prefix = {
        "warning": "warning: ",
        "stop": "stop: ",
        "feedback": "feedback: ",
        "context": "hook context: ",
        "error": "error: ",
    }.get(entry.kind, f"{entry.kind}: ")

    source = entry.text.split("\n")
    first  = source[0] if source else ""
    text   = f"    {prefix}{first}"

    if len(source) > 1:
        text += "\n" + "\n".join(
            f"    {line}" if line else ""
            for line in source[1:]
        )
    return [TextSpan(text)]


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


if __name__ == '__main__':
    pass
