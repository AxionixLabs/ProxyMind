# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.tools.plan_update import UPDATE_PLAN_TOOL
from agent.application.views.contracts import PresentationSink
from agent.application.views.builders.plan import build_plan_update_view
from agent.application.views.builders.tools import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view
)
from agent.domain.tool_policy import is_approval_only_tool
from .plan_steps import PlanExecutionReport
from .run import ToolRunResult

ToolDisplayResult = ToolRunResult | PlanExecutionReport


async def show_tool_start(
    presentation: PresentationSink,
    name: str,
    arguments: dict[str, typing.Any],
    *,
    call_id: str = "",
    patch_preview: dict[str, typing.Any] | None = None
) -> None:
    """发送普通工具开始执行的结构化展示数据。"""
    if is_approval_only_tool(name):
        return None
    if name in {UPDATE_PLAN_TOOL, "write_stdin"}:
        return None

    if name == "apply_patch" and not isinstance(patch_preview, dict):
        return None

    try:
        view = build_tool_start_view(
            name,
            arguments,
            patch_preview=patch_preview,
            call_id=call_id,
        )
    except (KeyError, TypeError, ValueError):
        if name == "apply_patch" and isinstance(patch_preview, dict):
            return None
        raise
    await presentation.emit(view)


async def show_tool_result(
    presentation: PresentationSink,
    name: str,
    arguments: dict[str, typing.Any],
    tool_run: ToolDisplayResult,
    *,
    ok: typing.Optional[bool] = None,
    text: typing.Optional[str] = None,
    use_coding_trace: bool = False,
    call_id: str = ""
) -> None:
    """发送工具结果的结构化展示数据。"""
    if is_approval_only_tool(name):
        return None
    display_ok   = tool_run.ok if ok is None else ok
    display_text = tool_run.text if text is None else str(text or "")

    if name == UPDATE_PLAN_TOOL and display_ok:
        view = build_plan_update_view(tool_run.data)
        if view is not None:
            await presentation.emit(view)
            return None

    if use_coding_trace:
        await presentation.emit(build_native_tool_result_view(
            name,
            arguments,
            ok=display_ok,
            data=tool_run.data,
            cost_ms=tool_run.cost_ms,
            call_id=call_id,
        ))
        return None

    if not display_text:
        return None

    await presentation.emit(build_generic_tool_result_view(
        name,
        display_text,
        ok=display_ok,
        call_id=call_id,
    ))


if __name__ == '__main__':
    pass
