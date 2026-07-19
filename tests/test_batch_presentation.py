# -*- coding: utf-8 -*-

import asyncio

from mind_app.presentation.batch_views import (
    build_batch_completed_view,
    build_batch_start_view,
)
from mind_app.presentation.rich import (
    render_batch_completed_view,
    render_batch_start_view,
)
from mind_app.runtime.tools.batch import (
    BatchToolResult,
    PendingToolCall,
    ToolCallBatch,
)
from mind_app.runtime.tools.batch_display import (
    should_group_batch,
    show_tool_batch_completed,
    show_tool_batch_start,
)


class FakeOutput(object):
    """记录 batch 展示产生的审计和输出。"""

    def __init__(self) -> None:
        self.audits: list[tuple[str, dict, str | None]] = []
        self.feeds: list[tuple[str, dict]] = []

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict,
        *,
        call_id: str | None = None,
    ) -> None:
        self.audits.append((name, arguments, call_id))

    async def feed(self, text: str, **kwargs) -> None:
        self.feeds.append((text, kwargs))


def pending_call(
    name: str,
    arguments: dict,
    *,
    call_id: str,
    coding: bool = False,
) -> PendingToolCall:
    """构造 batch 展示测试使用的待执行调用。"""
    return PendingToolCall(
        event={"call_id": call_id},
        name=name,
        arguments=arguments,
        meta=None,
        execution=None,
        use_coding_trace=coding,
    )


def test_batch_start_view_preserves_tree_and_argument_snapshot() -> None:
    """batch 开始 View 保持参数快照和当前树形布局。"""
    first_arguments = {"path": "/tmp/demo", "count": 2}
    view = build_batch_start_view([
        ("first_tool", first_arguments),
        ("second_tool", {}),
    ])
    rendered = render_batch_start_view(view)

    first_arguments["count"] = 3

    assert view.calls[0].arguments == {"path": "/tmp/demo", "count": 2}
    assert rendered.text == (
        "• Parallel tools\n"
        "├ first_tool\n"
        "│  └ count=2\n"
        "│  └ path='/tmp/demo'\n"
        "└ second_tool\n"
        "   └ no args"
    )
    assert rendered.preserve_display_parts is True
    assert "".join(part["text"] for part in rendered.display_parts) == rendered.text


def test_batch_completed_view_preserves_failure_status() -> None:
    """batch 完成 View 保持结果摘要和失败状态样式。"""
    view = build_batch_completed_view([
        ("first_tool", True, "done"),
        ("second_tool", False, "failed to run"),
    ])
    rendered = render_batch_completed_view(view)

    assert rendered.text == (
        "• Parallel tools completed\n"
        "├ first_tool  ok\n"
        "│  └ done\n"
        "└ second_tool  failed\n"
        "   └ failed to run"
    )
    assert rendered.display_parts[0] == {
        "text": "•",
        "style": "bold #FF6B6B",
    }


def test_batch_runtime_keeps_grouping_and_audit_behavior() -> None:
    """batch 运行时继续负责聚合条件和每个工具的参数审计。"""
    calls = [
        pending_call("first_tool", {"value": 1}, call_id="call-1"),
        pending_call("second_tool", {"value": 2}, call_id="call-2"),
    ]
    batch = ToolCallBatch(
        batch_id="batch-1",
        call_ids=["call-1", "call-2"],
        count=2,
        ready=True,
        timeout_sec=None,
        calls=calls,
    )
    output = FakeOutput()

    assert should_group_batch(batch) is True
    asyncio.run(show_tool_batch_start(output, batch))

    assert output.audits == [
        ("first_tool", {"value": 1}, "call-1"),
        ("second_tool", {"value": 2}, "call-2"),
    ]
    assert len(output.feeds) == 1
    assert output.feeds[0][1]["preserve_display_parts"] is True

    batch.calls[1].use_coding_trace = True
    assert should_group_batch(batch) is False


def test_single_batch_result_still_skips_grouped_display() -> None:
    """单个工具结果仍不进入 batch 聚合完成展示。"""
    result = BatchToolResult(
        name="only_tool",
        arguments={},
        ok=True,
        text="done",
    )
    output = FakeOutput()

    asyncio.run(show_tool_batch_completed(output, [result]))

    assert output.feeds == []
