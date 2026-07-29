# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from mind_app.output.content import (
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
)
from mind_app.output.terminal_content import TerminalContentSink
from mind_app.tui.adapters.content import TuiContentSink


def _output() -> SimpleNamespace:
    return SimpleNamespace(
        settle_stream=AsyncMock(),
        mark_stream_boundary=Mock(),
        prepare_external_output=AsyncMock(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("sink_type", (TerminalContentSink, TuiContentSink))
async def test_content_adapter_projects_assistant_segment_completion(
    sink_type,
) -> None:
    output = _output()
    sink = sink_type(output)

    await sink.emit(AssistantSegmentCompleted())

    output.settle_stream.assert_awaited_once_with()
    output.mark_stream_boundary.assert_called_once_with()
    output.prepare_external_output.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("sink_type", (TerminalContentSink, TuiContentSink))
async def test_content_adapter_projects_external_output_boundary(
    sink_type,
) -> None:
    output = _output()
    sink = sink_type(output)

    await sink.emit(AssistantOutputBoundary())

    output.prepare_external_output.assert_awaited_once_with()
    output.settle_stream.assert_not_awaited()
    output.mark_stream_boundary.assert_not_called()
