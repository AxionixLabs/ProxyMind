# -*- coding: utf-8 -*-

import io
import json

import pytest

from mind_app.output.content import (
    AssistantSegmentCompleted,
    AssistantTextDelta,
)
from mind_app.output.jsonl import (
    JsonContentSink,
    JsonOutputState,
)


class _RecordWriter(object):
    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def write_raw(self, text: str) -> None:
        _ = text

    def flush(self) -> None:
        return None


@pytest.mark.anyio
async def test_json_output_initializes_and_flushes_assistant_state() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)

    await content.emit(AssistantTextDelta("first "))
    await content.emit(AssistantTextDelta("second"))

    assert stdout.getvalue() == ""
    await content.emit(AssistantSegmentCompleted())

    event = json.loads(stdout.getvalue())
    assert event["item"]["text"] == "first second"


@pytest.mark.anyio
async def test_json_output_preserves_structured_text_semantics() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)
    raw = "id\tdevice\x1b]52;c;payload\x1b\\"

    await content.emit(AssistantTextDelta(raw))
    await content.emit(AssistantSegmentCompleted())

    event = json.loads(stdout.getvalue())
    assert event["item"]["text"] == raw
