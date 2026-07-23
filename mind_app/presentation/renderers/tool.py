# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.stream_events.tool_traces.render import render_tool_trace_parts
from ..models import (
    GenericToolResultView,
    NativeToolResultView,
    StyledBlock,
    ToolStartView,
    TracePreview
)


def render_tool_start_view(view: ToolStartView) -> StyledBlock:
    """把普通工具开始视图转换为中立展示块。"""
    return StyledBlock(
        plain_text=view.title,
        spans=tuple(render_tool_trace_parts(
            view.title,
            preview=view.preview,
            ok=None,
        )),
    )


def render_generic_tool_result_view(view: GenericToolResultView) -> StyledBlock:
    """把普通工具结果视图转换为中立展示块。"""
    return StyledBlock(
        plain_text=_generic_trace_text(view.title, view.preview),
        spans=tuple(render_tool_trace_parts(
            view.title,
            preview=view.preview,
            ok=view.ok,
        )),
        preserve_spans=True,
    )


def render_native_tool_result_view(
    view: NativeToolResultView,
    *,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> tuple[StyledBlock, ...]:
    """把原生编码工具结果视图转换为中立展示块。"""
    return tuple(
        StyledBlock(
            plain_text=_coding_trace_text(entry.title, entry.preview),
            spans=tuple(render_tool_trace_parts(
                entry.title,
                preview=entry.preview,
                ok=entry.ok,
                terminal_width=terminal_width,
                measure_width=measure_width,
            )),
            preserve_spans=True,
        )
        for entry in view.entries
    )


def _generic_trace_text(title: str, preview: TracePreview) -> str:
    """生成普通工具结果的记录文本。"""
    if not preview.full:
        return title

    indented_preview = preview.full.replace("\n", "\n  ")
    return f"{title}\n└ {indented_preview}"


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
