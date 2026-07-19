# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.output import OutputControlPort
from mind_app.presentation.batch_views import (
    build_batch_completed_view,
    build_batch_start_view
)
from mind_app.presentation.contracts import PresentationSink
from .batch import BatchToolResult, ToolCallBatch


def should_group_batch(batch: ToolCallBatch) -> bool:
    """判断 batch 是否需要聚合展示。"""
    return len(batch.calls) > 1 and not any(
        call.use_coding_trace for call in batch.calls
    )


async def show_tool_batch_start(
    presentation: PresentationSink,
    audit_output: OutputControlPort,
    batch: ToolCallBatch,
) -> None:
    """展示一批工具调用的聚合开始块。"""
    if not should_group_batch(batch):
        return None

    for call in batch.calls:
        audit_output.record_tool_arguments(
            call.name,
            call.arguments,
            call_id=str(call.event.get("call_id") or ""),
        )

    await presentation.emit(build_batch_start_view(
        (call.name, call.arguments) for call in batch.calls
    ))


async def show_tool_batch_completed(
    presentation: PresentationSink,
    results: list[BatchToolResult],
) -> None:
    """展示一批工具调用的聚合完成块。"""
    if len(results) <= 1:
        return None

    await presentation.emit(build_batch_completed_view(
        (result.name, result.ok, result.text) for result in results
    ))


if __name__ == '__main__':
    pass
