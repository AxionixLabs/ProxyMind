# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp import types as mcp_types

from mind_app.presentation.legacy import LegacyPresentationSink
from mind_app.presentation.models import ProgressView
from mind_app.runtime.tools import run as run_module


def test_legacy_progress_view_keeps_unstyled_block_output() -> None:
    """默认进度适配器保持无样式 block 输出。"""
    output = SimpleNamespace(
        feed=AsyncMock(),
        print_block=AsyncMock(),
    )
    presentation = LegacyPresentationSink(output)

    asyncio.run(presentation.emit(ProgressView(
        text="working",
        source="tool",
        tool_name="coding",
    )))

    output.feed.assert_awaited_once_with(
        "working",
        display="block",
        display_parts=None,
        preserve_display_parts=False,
    )
    output.print_block.assert_not_awaited()


def test_run_tool_step_sends_tool_and_enhancement_progress(monkeypatch) -> None:
    """工具执行入口把两类可见进度发送到结构化展示端。"""
    output = SimpleNamespace(
        begin_tool_status=AsyncMock(),
        begin_custom_tool_status=AsyncMock(),
        end_status=AsyncMock(),
        feed=AsyncMock(),
    )
    presentation = SimpleNamespace(emit=AsyncMock())

    async def fake_execute_tool(*_args, stream_callback, **_kwargs):
        await stream_callback("tool working")
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="done")],
            structuredContent={
                "ok": True,
                "text": "done",
                "data": {},
            },
            isError=False,
        )

    async def fake_enhance_result(*, reporter, **_kwargs):
        await reporter.display("enhancing result")
        return {
            "ok": True,
            "text": "done",
            "data": {},
        }

    monkeypatch.setattr(run_module, "execute_tool", fake_execute_tool)
    monkeypatch.setattr(run_module, "enhance_result", fake_enhance_result)

    result = asyncio.run(run_module.run_tool_step(
        SimpleNamespace(),
        stream_ui=output,
        presentation=presentation,
        tools=[],
        name="coding",
        arguments={},
        meta=None,
        pref_config={},
        enable_progress_notify=True,
    ))

    assert result.ok is True
    assert result.text == "done"
    assert presentation.emit.await_args_list == [
        ((ProgressView(
            text="tool working",
            source="tool",
            tool_name="coding",
        ),), {}),
        ((ProgressView(
            text="enhancing result",
            source="enhancement",
            tool_name="coding",
        ),), {}),
    ]
    output.begin_tool_status.assert_awaited_once_with()
    output.begin_custom_tool_status.assert_not_awaited()
    output.end_status.assert_awaited_once_with()
    output.feed.assert_not_awaited()


if __name__ == '__main__':
    pass
