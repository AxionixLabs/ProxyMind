# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from ..stream_ui import StreamUI
from ..stream_events.tool_trace import (
    MISSING,
    render_generic_tool_result_parts,
    render_generic_tool_result_preview,
    render_tool_result_preview,
    render_tool_start_preview,
    render_tool_start_trace,
    render_tool_trace,
    render_tool_trace_parts
)

if typing.TYPE_CHECKING:
    from .tool_run import ToolRunResult


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
        f"{trace_start}\n",
        display=StreamUI.BLOCK,
        display_chunk=trace_start,
        display_parts=render_tool_trace_parts(
            trace_start,
            preview=render_tool_start_preview(arguments)
        )
    )


async def show_tool_result(
    stream_ui: StreamUI,
    name: str,
    arguments: dict[str, typing.Any],
    tool_run: "ToolRunResult",
    *,
    ok: typing.Optional[bool] = None,
    fields: typing.Optional[typing.Union[str, dict[str, typing.Any]]] = None,
    text: typing.Optional[str] = None,
    use_coding_trace: bool = False,
    before_exists: typing.Any = MISSING
) -> None:
    """显示工具结果轨迹；两条模式链路共享同一套渲染入口。"""
    display_ok = tool_run.ok if ok is None else ok

    _ = tool_run.fields if fields is None else fields

    display_text = tool_run.text if text is None else str(text or "")

    if use_coding_trace:
        await stream_ui.end_status()
        trace_title = render_tool_trace(
            name,
            arguments,
            ok=display_ok,
            data=tool_run.data,
            cost_ms=tool_run.cost_ms,
            before_exists=before_exists
        )
        trace_preview = render_tool_result_preview(
            name,
            tool_run.data,
            arguments=arguments
        )

        trace_text = trace_title
        if trace_preview.full:
            indented_preview = trace_preview.full.replace("\n", "\n  ")
            trace_text = f"{trace_title}\n└ {indented_preview}"

        await stream_ui.feed(
            f"{trace_text}\n",
            display=StreamUI.BLOCK,
            display_parts=render_tool_trace_parts(
                trace_title,
                preview=trace_preview,
                ok=display_ok
            )
        )
        return None

    if not display_text:
        return None

    trace_preview = render_generic_tool_result_preview(display_text)
    await stream_ui.feed(
        display_text,
        display=StreamUI.BLOCK,
        display_parts=render_generic_tool_result_parts(trace_preview, ok=display_ok)
    )


if __name__ == '__main__':
    pass
