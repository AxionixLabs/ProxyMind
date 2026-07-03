# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.design import Design
from ..stream_ui import StreamUI
from ..stream_events.tool_trace import (
    render_generic_tool_result_preview,
    render_tool_result_entries,
    render_tool_start_preview,
    render_tool_start_trace,
    render_tool_trace_parts
)

if typing.TYPE_CHECKING:
    from .tool_run import ToolDisplayResult


def _coding_trace_text(
    title: str,
    preview: typing.Any
) -> str:
    """生成用于记录的编码工具轨迹文本。"""
    trace_text = title
    if preview.full:
        if preview.kind != "tree":
            indented_preview = preview.full.replace("\n", "\n  ")
            trace_text = f"{title}\n└ {indented_preview}"
        else:
            trace_text = f"{title}\n{preview.full}"
    return trace_text


def _generic_trace_text(
    title: str,
    preview: typing.Any
) -> str:
    """生成普通工具结果的文本轨迹。"""
    trace_text = title
    if preview.full:
        indented_preview = preview.full.replace("\n", "\n  ")
        trace_text = f"{title}\n└ {indented_preview}"
    return trace_text


async def show_tool_start(
    stream_ui: StreamUI,
    name: str,
    arguments: dict[str, typing.Any],
    *,
    call_id: typing.Optional[str] = None,
    audit: bool = True
) -> None:
    """显示普通工具开始执行轨迹，并按需记录完整参数审计。"""
    if audit:
        stream_ui.record_tool_arguments(name, arguments, call_id=call_id)

    trace_start = render_tool_start_trace(name, arguments)

    await stream_ui.feed(
        trace_start,
        display=StreamUI.BLOCK,
        display_parts=render_tool_trace_parts(
            trace_start,
            preview=render_tool_start_preview(arguments)
        )
    )


async def show_tool_result(
    stream_ui: StreamUI,
    name: str,
    arguments: dict[str, typing.Any],
    tool_run: "ToolDisplayResult",
    *,
    ok: typing.Optional[bool] = None,
    fields: typing.Optional[typing.Union[str, dict[str, typing.Any]]] = None,
    text: typing.Optional[str] = None,
    use_coding_trace: bool = False
) -> None:
    """显示工具结果轨迹；两条模式链路共享同一套渲染入口。"""
    display_ok = tool_run.ok if ok is None else ok

    _ = tool_run.fields if fields is None else fields

    display_text = tool_run.text if text is None else str(text or "")

    if use_coding_trace:
        await stream_ui.end_status()
        trace_entries = render_tool_result_entries(
            name,
            arguments,
            ok=display_ok,
            data=tool_run.data,
            cost_ms=tool_run.cost_ms
        )

        for entry in trace_entries:
            await stream_ui.feed(
                _coding_trace_text(entry.title, entry.preview),
                display=StreamUI.BLOCK,
                display_parts=render_tool_trace_parts(
                    entry.title,
                    preview=entry.preview,
                    ok=entry.ok,
                    terminal_width=getattr(Design.console, "width", None)
                ),
                preserve_display_parts=True
            )
        return None

    if not display_text:
        return None

    title         = f"• Tool {str(name or 'tool').strip() or 'tool'}"
    trace_preview = render_generic_tool_result_preview(display_text)

    await stream_ui.feed(
        _generic_trace_text(title, trace_preview),
        display=StreamUI.BLOCK,
        display_parts=render_tool_trace_parts(title, preview=trace_preview, ok=display_ok),
        preserve_display_parts=True
    )


if __name__ == '__main__':
    pass
