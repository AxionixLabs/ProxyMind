# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.output import (
    BLOCK_OUTPUT,
    OutputPort
)
from mind_app.presentation.batch_views import (
    build_batch_completed_view,
    build_batch_start_view,
)
from mind_app.presentation.rich import (
    render_batch_completed_view,
    render_batch_start_view,
)
from .batch import BatchToolResult, ToolCallBatch


def should_group_batch(batch: ToolCallBatch) -> bool:
    """判断 batch 是否需要聚合展示。"""
    return len(batch.calls) > 1 and not any(
        call.use_coding_trace for call in batch.calls
    )


async def show_tool_batch_start(
    stream_ui: OutputPort,
    batch: ToolCallBatch,
) -> None:
    """展示一批工具调用的聚合开始块。"""
    if not should_group_batch(batch):
        return None

    for call in batch.calls:
        stream_ui.record_tool_arguments(
            call.name,
            call.arguments,
            call_id=str(call.event.get("call_id") or ""),
        )

    rendered = render_batch_start_view(build_batch_start_view(
        (call.name, call.arguments) for call in batch.calls
    ))
    await stream_ui.feed(
        rendered.text,
        display=BLOCK_OUTPUT,
        display_parts=list(rendered.display_parts),
        preserve_display_parts=rendered.preserve_display_parts,
    )


async def show_tool_batch_completed(
    stream_ui: OutputPort,
    results: list[BatchToolResult],
) -> None:
    """展示一批工具调用的聚合完成块。"""
    if len(results) <= 1:
        return None

    rendered = render_batch_completed_view(build_batch_completed_view(
        (result.name, result.ok, result.text) for result in results
    ))
    await stream_ui.feed(
        rendered.text,
        display=BLOCK_OUTPUT,
        display_parts=list(rendered.display_parts),
        preserve_display_parts=rendered.preserve_display_parts,
    )


if __name__ == '__main__':
    pass
