# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.client_tools.update_plan import UPDATE_PLAN_TOOL
from mind_app.presentation.rich import (
    RenderedBlock,
    render_generic_tool_result_view,
    render_native_tool_result_view,
    render_tool_start_view,
)
from mind_app.presentation.tool_views import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)
from ...output import (
    BLOCK_OUTPUT, OutputPort
)
from .plan_update_display import render_plan_update
from .plan_steps import PlanExecutionReport
from .run import ToolRunResult

ToolDisplayResult = ToolRunResult | PlanExecutionReport


async def _show_rendered_tool_block(
    stream_ui: OutputPort,
    rendered: RenderedBlock,
) -> None:
    """通过当前输出端展示已渲染的工具块。"""
    await stream_ui.feed(
        rendered.text,
        display=BLOCK_OUTPUT,
        display_parts=list(rendered.display_parts),
        preserve_display_parts=rendered.preserve_display_parts,
    )


async def show_tool_start(
    stream_ui: OutputPort,
    name: str,
    arguments: dict[str, typing.Any],
    *,
    call_id: typing.Optional[str] = None,
    audit: bool = True
) -> None:
    """显示普通工具开始执行轨迹，并按需记录完整参数审计。"""
    if audit:
        stream_ui.record_tool_arguments(name, arguments, call_id=call_id)

    if name == UPDATE_PLAN_TOOL:
        return None

    await _show_rendered_tool_block(
        stream_ui,
        render_tool_start_view(build_tool_start_view(name, arguments)),
    )


async def show_tool_result(
    stream_ui: OutputPort,
    name: str,
    arguments: dict[str, typing.Any],
    tool_run: ToolDisplayResult,
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

    if name == UPDATE_PLAN_TOOL and display_ok:
        rendered = render_plan_update(tool_run.data)
        if rendered is not None:
            plan_text, plan_parts = rendered
            await stream_ui.feed(
                plan_text,
                display=BLOCK_OUTPUT,
                display_parts=plan_parts,
                preserve_display_parts=True
            )
            return None

    if use_coding_trace:
        await stream_ui.end_status()
        view = build_native_tool_result_view(
            name,
            arguments,
            ok=display_ok,
            data=tool_run.data,
            cost_ms=tool_run.cost_ms,
        )

        for rendered in render_native_tool_result_view(view):
            await _show_rendered_tool_block(
                stream_ui,
                rendered,
            )
        return None

    if not display_text:
        return None

    await _show_rendered_tool_block(
        stream_ui,
        render_generic_tool_result_view(
            build_generic_tool_result_view(
                name,
                display_text,
                ok=display_ok,
            )
        ),
    )


if __name__ == '__main__':
    pass
