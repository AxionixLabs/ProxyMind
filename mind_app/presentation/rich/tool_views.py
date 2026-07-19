# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.stream_events.tool_traces.render import render_tool_trace_parts
from ..models import (
    GenericToolResultView,
    ToolStartView,
    TracePreview
)
from .models import RenderedBlock


def render_tool_start_view(view: ToolStartView) -> RenderedBlock:
    """把普通工具开始视图转换为当前终端展示。"""
    return RenderedBlock(
        text=view.title,
        display_parts=tuple(
            render_tool_trace_parts(view.title, preview=view.preview)
        ),
    )


def render_generic_tool_result_view(
    view: GenericToolResultView,
) -> RenderedBlock:
    """把普通工具结果视图转换为当前终端展示。"""
    return RenderedBlock(
        text=_generic_trace_text(view.title, view.preview),
        display_parts=tuple(
            render_tool_trace_parts(
                view.title,
                preview=view.preview,
                ok=view.ok,
            )
        ),
        preserve_display_parts=True,
    )


def _generic_trace_text(title: str, preview: TracePreview) -> str:
    """生成普通工具结果的记录文本。"""
    if not preview.full:
        return title

    indented_preview = preview.full.replace("\n", "\n  ")
    return f"{title}\n└ {indented_preview}"


if __name__ == '__main__':
    pass
