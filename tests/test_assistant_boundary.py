# -*- coding: utf-8 -*-

from mind_app.stream_events.assistant_boundary import is_assistant_output_boundary
from protocol.schema.stream_events import StreamEvent


def test_display_text_is_not_an_assistant_identity_boundary() -> None:
    event = StreamEvent(
        type="provider.progress",
        display={"text": "loading"},
    )

    assert is_assistant_output_boundary(event) is False


def test_structured_tool_event_is_an_assistant_identity_boundary() -> None:
    event = StreamEvent(type="tool.call")

    assert is_assistant_output_boundary(event) is True
