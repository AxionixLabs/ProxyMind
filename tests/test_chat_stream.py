# -*- coding: utf-8 -*-

from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from mind_nova.requests import chat
from mind_nova.stream_events import TextDeltaEvent


@pytest.mark.anyio
async def test_stream_chat_parses_events_and_filters_ping(monkeypatch) -> None:
    async def streaming(*_args, **_kwargs):
        yield {"type": "ping"}
        yield {
            "type": "text.delta",
            "segment_id": "segment-1",
            "text": "answer",
        }

    build_payload = AsyncMock(return_value={"message": "hello"})
    make_headers = Mock(return_value={"authorization": "test"})
    endpoint = Mock(return_value="https://example.com/chat")
    monkeypatch.setattr(chat, "build_chat_payload", build_payload)
    monkeypatch.setattr(chat.Channel, "make_headers", make_headers)
    monkeypatch.setattr(chat.service_endpoints, "endpoint", endpoint)
    monkeypatch.setattr(chat, "streaming", streaming)

    events = [
        event async for event in chat.stream_chat(
            {},
            "hello",
            [],
            timeout=12.0,
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], TextDeltaEvent)
    assert events[0].segment_id == "segment-1"
    assert events[0].text == "answer"
    build_payload.assert_awaited_once()
    endpoint.assert_called_once_with("/mind-chat")
    make_headers.assert_called_once_with()
