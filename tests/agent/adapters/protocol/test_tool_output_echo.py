from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent.adapters.protocol import tool_events
from protocol.schema.stream_events import ToolOutputEvent


@pytest.mark.anyio
@pytest.mark.parametrize("display", [False, True])
async def test_local_result_echo_does_not_duplicate_transcript_or_display(monkeypatch, display) -> None:
    show = AsyncMock()
    monkeypatch.setattr(tool_events, "show_tool_result", show)
    run = SimpleNamespace(status="completed", ok=True, cost_ms=1, fields={})
    handler = SimpleNamespace(
        tool_execution=SimpleNamespace(project_server_output=Mock(return_value=run)),
        transcript=SimpleNamespace(append=Mock()), presentation=Mock(),
    )
    event = ToolOutputEvent(
        type="tool.output", proto="mind.chat", cid="c", sid="s", turn_id="t",
        presentation_epoch=1, call_id="call", name="read_file", payload={"display": display, "status": "completed"},
    )
    await tool_events.ToolEventHandler.handle_output(handler, event)
    assert handler.transcript.append.call_count == int(display)
    assert show.await_count == int(display)
