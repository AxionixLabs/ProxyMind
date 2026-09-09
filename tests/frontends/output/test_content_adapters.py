# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from agent.ports import (
    AssistantOutputBoundary,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    OutputSurfaceContext,
    ResponseIdentity,
)
from frontends.output.terminal_content import TerminalContentSink
from frontends.tui.adapters.content import TuiContentSink


def _output() -> SimpleNamespace:
    return SimpleNamespace(
        settle_stream=AsyncMock(),
        mark_stream_boundary=Mock(),
        prepare_assistant_output=AsyncMock(),
        prepare_external_output=AsyncMock(),
    )


def _identity() -> ResponseIdentity:
    return ResponseIdentity("turn_test", 1, 1, 1)


def _surface_context() -> OutputSurfaceContext:
    """构造 TUI content adapter 必需的输出 scope。"""
    return OutputSurfaceContext(
        surface_id="surface_test",
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        agent_id="root",
    )


def _content_sink(sink_type, output):
    """按 adapter 的真实构造契约创建测试 sink。"""
    if sink_type is TuiContentSink:
        return sink_type(output, surface_context=_surface_context())
    return sink_type(output)


@pytest.mark.anyio
@pytest.mark.parametrize("sink_type", (TerminalContentSink, TuiContentSink))
async def test_content_adapter_projects_assistant_segment_completion(
    sink_type,
) -> None:
    output = _output()
    sink = _content_sink(sink_type, output)

    await sink.emit(AssistantSegmentCompleted(_identity()))

    output.settle_stream.assert_awaited_once_with()
    output.mark_stream_boundary.assert_called_once_with()
    output.prepare_external_output.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("sink_type", (TerminalContentSink, TuiContentSink))
async def test_content_adapter_projects_external_output_boundary(
    sink_type,
) -> None:
    output = _output()
    sink = _content_sink(sink_type, output)

    await sink.emit(AssistantOutputBoundary())

    output.prepare_external_output.assert_awaited_once_with()
    output.settle_stream.assert_not_awaited()
    output.mark_stream_boundary.assert_not_called()


@pytest.mark.anyio
async def test_tui_content_adapter_projects_response_retry_boundary() -> None:
    """验证 response 重试沿用原子的 TUI 正文切换路径。"""
    output = SimpleNamespace(supersede_assistant_presentation=AsyncMock())
    sink = TuiContentSink(output, surface_context=_surface_context())

    await sink.emit(AssistantResponseSuperseded(
        turn_id="turn_test",
        presentation_epoch=1,
        round=2,
        attempt=2,
    ))

    output.supersede_assistant_presentation.assert_awaited_once_with()


@pytest.mark.anyio
async def test_terminal_content_adapter_projects_response_retry_boundary() -> None:
    """验证非 TUI 终端在 response 重试时提交旧正文并输出单条提示。"""
    output = SimpleNamespace(
        prepare_external_output=AsyncMock(),
        feed=AsyncMock(),
    )
    sink = TerminalContentSink(output)

    await sink.emit(AssistantResponseSuperseded(
        turn_id="turn_test",
        presentation_epoch=1,
        round=2,
        attempt=2,
    ))

    output.prepare_external_output.assert_awaited_once_with()
    output.feed.assert_awaited_once()
