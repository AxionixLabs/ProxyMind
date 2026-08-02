# -*- coding: utf-8 -*-

import pytest

from mind_app.history.transcript import TranscriptEntry
from mind_app.tui.features import history


@pytest.mark.anyio
async def test_history_menu_displays_date_then_query(monkeypatch) -> None:
    record = {
        "cid": "conversation-id",
        "title": "explain the current architecture",
        "workspace": r"D:\PycharmProjects\ProxyMind",
        "updated_at": 1,
    }
    requests = []

    class Runtime(object):
        async def select_menu(self, request):
            requests.append(request)
            return request.options[0].value

    monkeypatch.setattr(
        history,
        "_format_updated_at",
        lambda _value: "07-21 14:30",
    )

    selected = await history.choose_history_session(Runtime(), [record])

    assert selected is record
    option = requests[0].options[0]
    assert option.label == "07-21 14:30"
    assert option.detail == "explain the current architecture"


@pytest.mark.anyio
async def test_history_menu_can_include_workspace(monkeypatch) -> None:
    record = {
        "cid": "conversation-id",
        "title": "continue the task",
        "workspace": r"D:\PycharmProjects\ProxyMind",
        "updated_at": 1,
    }
    requests = []

    class Runtime(object):
        async def select_menu(self, request):
            requests.append(request)
            return request.options[0].value

    monkeypatch.setattr(
        history,
        "_format_updated_at",
        lambda _value: "07-21 14:30",
    )

    await history.choose_history_session(
        Runtime(),
        [record],
        show_workspace=True,
    )

    assert requests[0].options[0].detail == (
        r"continue the task · D:\PycharmProjects\ProxyMind"
    )


def test_history_transcript_replays_messages_and_tool_result() -> None:
    def entry(
        event: str,
        *,
        actor: str,
        payload: dict,
    ) -> TranscriptEntry:
        return TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event=event,
            session_id="session_test",
            turn_id="turn_test",
            actor=actor,
            payload=payload,
        )

    entries = (
        entry(
            "message.created",
            actor="user",
            payload={
                "content": "show the workspace",
                "attachments": [{"filename": "screen.png"}],
                "extras": {"selection": "src/app.py"},
            },
        ),
        entry(
            "tool.started",
            actor="tool",
            payload={
                "call_id": "call_1",
                "name": "shell_command",
                "arguments": {"command": "pwd"},
            },
        ),
        entry(
            "tool.completed",
            actor="tool",
            payload={
                "call_id": "call_1",
                "result": {"output": "D:/workspace"},
            },
        ),
        entry(
            "message.created",
            actor="assistant",
            payload={"content": "**Done**"},
        ),
        entry(
            "context.compacted",
            actor="system",
            payload={"summary": "Context compacted · 20 -> 4 items"},
        ),
    )

    class Controller(object):
        def read_conversation_transcript(self, session_id):
            assert session_id == "session_test"
            return entries

    blocks = history.load_history_transcript(
        Controller(),
        "session_test",
        terminal_width=60,
    )

    assert [block.kind for block in blocks] == [
        "user",
        "operation",
        "assistant",
        "notice",
    ]
    assert blocks[0].turn_id == "turn_test"
    assert blocks[0].prompt == "show the workspace"
    assert blocks[0].attachments == ({"filename": "screen.png"},)
    assert blocks[0].extras == {"selection": "src/app.py"}
    assert blocks[0].source is entries[0]
    assert blocks[0].raw_text == "show the workspace"
    assert isinstance(blocks[1].source, TranscriptEntry)
    assert blocks[1].source.event == "tool.completed"
    assert blocks[1].raw_text == "pwd\nD:/workspace"
    assert blocks[2].source is entries[3]
    assert blocks[2].raw_text == "**Done**"
    assert blocks[3].source is entries[4]
    assert blocks[3].raw_text == "Context compacted · 20 -> 4 items"
    assert "pwd" in "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert "D:/workspace" in "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert "Done" in "".join(
        text for _style, text in blocks[2].display_block.fragments
    )
    assert "20 -> 4" in "".join(
        text for _style, text in blocks[3].display_block.fragments
    )


def test_history_transcript_falls_back_to_legacy_cursor_title() -> None:
    class Controller(object):
        @staticmethod
        def read_conversation_transcript(_session_id):
            return ()

    blocks = history.load_history_transcript(
        Controller(),
        "session_legacy",
        terminal_width=60,
        record={"title": "legacy question"},
    )

    assert len(blocks) == 1
    assert blocks[0].kind == "user"
    assert blocks[0].prompt == "legacy question"
    assert blocks[0].raw_text == "legacy question"


def test_history_transcript_uses_placeholder_when_content_is_unavailable() -> None:
    class Controller(object):
        @staticmethod
        def read_conversation_transcript(_session_id):
            return ()

    blocks = history.load_history_transcript(
        Controller(),
        "session_missing",
        terminal_width=60,
    )

    assert len(blocks) == 1
    assert blocks[0].kind == "notice"
    assert blocks[0].raw_text == (
        "Earlier transcript content is unavailable (session_missing)."
    )


@pytest.mark.parametrize(
    ("event", "payload", "expected"),
    [
        ("turn.failed", {"error": "request failed"}, "■ request failed"),
        ("turn.interrupted", {}, "Turn interrupted"),
    ],
)
def test_history_transcript_marks_only_failures(event, payload, expected) -> None:
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event=event,
        session_id="session_notice",
        turn_id="turn_notice",
        actor="system",
        payload=payload,
    )

    class Controller(object):
        @staticmethod
        def read_conversation_transcript(_session_id):
            return (entry,)

    blocks = history.load_history_transcript(
        Controller(),
        "session_notice",
        terminal_width=60,
    )

    assert "".join(
        text for _style, text in blocks[0].display_block.fragments
    ) == expected


def test_history_transcript_restores_markdown_hyperlink_metadata() -> None:
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event="message.created",
        session_id="session_link",
        turn_id="turn_link",
        actor="assistant",
        payload={"content": "[docs](https://example.com/docs)"},
    )

    class Controller(object):
        @staticmethod
        def read_conversation_transcript(_session_id):
            return (entry,)

    linked = history.load_history_transcript(
        Controller(),
        "session_link",
        terminal_width=60,
        hyperlinks=True,
    )
    plain = history.load_history_transcript(
        Controller(),
        "session_link",
        terminal_width=60,
        hyperlinks=False,
    )

    assert linked[0].raw_text == "[docs](https://example.com/docs)"
    assert any(
        style == "[ZeroWidthEscape]"
        for style, _text in linked[0].transcript_block.fragments
    )
    assert all(
        style != "[ZeroWidthEscape]"
        for style, _text in plain[0].transcript_block.fragments
    )
