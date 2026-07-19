# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.stream_events.tool_traces.generic import render_generic_tool_result_preview
from mind_app.stream_events.tool_traces.native import (
    render_tool_start_preview,
    render_tool_start_trace,
)
from .models import (
    GenericToolResultView,
    ToolStartView
)


def build_tool_start_view(
    name: str,
    arguments: dict[str, typing.Any],
) -> ToolStartView:
    """构建普通工具开始执行时的展示数据。"""
    normalized_arguments = dict(arguments) if isinstance(arguments, dict) else {}

    return ToolStartView(
        name=str(name or "tool").strip() or "tool",
        arguments=normalized_arguments,
        title=render_tool_start_trace(name, normalized_arguments),
        preview=render_tool_start_preview(normalized_arguments),
    )


def build_generic_tool_result_view(
    name: str,
    text: typing.Any,
    *,
    ok: bool,
) -> GenericToolResultView:
    """构建普通工具执行结果的展示数据。"""
    normalized_name = str(name or "tool").strip() or "tool"
    normalized_text = str(text or "")

    return GenericToolResultView(
        name=normalized_name,
        text=normalized_text,
        ok=bool(ok),
        title=f"• Tool {normalized_name}",
        preview=render_generic_tool_result_preview(normalized_text),
    )


if __name__ == '__main__':
    pass
