# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_core.design import Design
from mind_app.stream_events.tool_traces.render import render_tool_trace_parts
from ..models import (
    NativeToolResultView,
    TracePreview
)
from .models import RenderedBlock


def render_native_tool_result_view(
    view: NativeToolResultView,
    *,
    terminal_width: int | None = None,
) -> tuple[RenderedBlock, ...]:
    """把原生编码工具结果视图转换为当前终端展示。"""
    width = (
        getattr(Design.console, "width", None)
        if terminal_width is None
        else terminal_width
    )

    return tuple(
        RenderedBlock(
            text=_coding_trace_text(entry.title, entry.preview),
            display_parts=tuple(render_tool_trace_parts(
                entry.title,
                preview=entry.preview,
                ok=entry.ok,
                terminal_width=width,
            )),
            preserve_display_parts=True,
        )
        for entry in view.entries
    )


def _coding_trace_text(title: str, preview: TracePreview) -> str:
    """生成原生编码工具结果的记录文本。"""
    if not preview.full:
        return title
    if preview.kind == "tree":
        return f"{title}\n{preview.full}"

    indented_preview = preview.full.replace("\n", "\n  ")
    return f"{title}\n└ {indented_preview}"


if __name__ == '__main__':
    pass
