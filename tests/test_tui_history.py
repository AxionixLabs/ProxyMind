# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.history.transcript import TranscriptEntry
from mind_app.tui.core.document import TuiDocument
from mind_app.tui.core.render import fragments_text
from mind_app.tui.features import history
from mind_app.tui.core.hyperlinks import terminal_hyperlink_from_style


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
                "duration_ms": 120,
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
    assert "✓ • 120ms" in "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    ).splitlines()[-1] == "✓ • 120ms"
    assert "Done" in "".join(
        text for _style, text in blocks[2].display_block.fragments
    )
    assert "20 -> 4" in "".join(
        text for _style, text in blocks[3].display_block.fragments
    )


def test_history_transcript_keeps_javascript_source_out_of_result_block() -> None:
    source = "const value = 1;"
    entries = (
        TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event="tool.started",
            session_id="session_test",
            turn_id="turn_test",
            actor="tool",
            payload={
                "call_id": "call_js",
                "name": "js_repl",
                "arguments": {"code": source},
            },
        ),
        TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event="tool.completed",
            session_id="session_test",
            turn_id="turn_test",
            actor="tool",
            payload={
                "call_id": "call_js",
                "ok": True,
                "result": {"output": "done"},
            },
        ),
    )

    class Controller(object):
        @staticmethod
        def read_conversation_transcript(_session_id):
            return entries

    blocks = history.load_history_transcript(
        Controller(),
        "session_test",
        terminal_width=80,
    )

    assert [block.kind for block in blocks] == ["operation", "operation"]
    assert blocks[0].raw_text == source
    assert blocks[1].raw_text == "done"
    first_transcript = "".join(
        text for _style, text in blocks[0].transcript_block.fragments
    )
    second_transcript = "".join(
        text for _style, text in blocks[1].transcript_block.fragments
    )
    assert source in first_transcript
    assert source not in second_transcript
    assert "done" in second_transcript


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
        (
            "turn.incomplete",
            {"error": "max_output_tokens"},
            "■ max_output_tokens",
        ),
        ("turn.interrupted", {}, "Turn interrupted"),
    ],
)
def test_history_transcript_marks_terminal_notices(event, payload, expected) -> None:
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
        terminal_hyperlink_from_style(style) == "https://example.com/docs"
        for style, _text in linked[0].transcript_block.fragments
    )
    assert all(
        style != "[ZeroWidthEscape]"
        for style, _text in plain[0].transcript_block.fragments
    )


def test_history_transcript_restores_responsive_markdown_tables() -> None:
    source = (
        "| Name | Status | Description |\n"
        "|---|---|---|\n"
        "| API | Ready | Service is available |"
    )
    entry = TranscriptEntry(
        timestamp="2026-08-02T00:00:00.000Z",
        event="message.created",
        session_id="session_table",
        turn_id="turn_table",
        actor="assistant",
        payload={"content": source},
    )

    class Controller(object):
        @staticmethod
        def read_conversation_transcript(_session_id):
            return (entry,)

    cell = history.load_history_transcript(
        Controller(),
        "session_table",
        terminal_width=24,
    )[0]
    narrow = "".join(text for _style, text in cell.display_block.fragments)

    assert cell.source_renderer is not None
    assert cell.source_render_width == 24
    assert "  Description" in narrow
    assert all(get_cwidth(line) <= 24 for line in narrow.splitlines())

    wide = cell.source_renderer(source, 60)
    wide_text = "".join(text for _style, text in wide.fragments)

    assert "━" in wide_text
    assert "Name  Status" in wide_text


def test_history_shell_display_reflows_without_changing_transcript() -> None:
    command = "python3 -c \"print('" + "界" * 80 + "')\""
    output_lines = [
        f"output {index} " + "👩\u200d💻" * 30
        for index in range(8)
    ]

    def entry(event: str, payload: dict) -> TranscriptEntry:
        return TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event=event,
            session_id="session_shell_resize",
            turn_id="turn_shell_resize",
            actor="tool",
            payload=payload,
        )

    entries = (
        entry("tool.started", {
            "call_id": "call_shell_resize",
            "name": "shell_command",
            "arguments": {"command": command},
        }),
        entry("tool.completed", {
            "call_id": "call_shell_resize",
            "result": {
                "command": command,
                "output_lines": output_lines,
            },
        }),
    )

    class Controller(object):
        @staticmethod
        def read_conversation_transcript(_session_id):
            return entries

    blocks = history.load_history_transcript(
        Controller(),
        "session_shell_resize",
        terminal_width=20,
    )
    document = TuiDocument()
    document.replace_blocks(blocks)

    narrow = fragments_text(document.fragments(width=20))
    transcript = fragments_text(document.transcript_fragments(width=80))
    wide = fragments_text(document.fragments(width=80))

    assert wide != narrow
    assert get_cwidth(narrow.splitlines()[0]) <= 20
    assert get_cwidth(wide.splitlines()[0]) <= 80
    assert get_cwidth(wide.splitlines()[0]) > get_cwidth(
        narrow.splitlines()[0]
    )
    assert "output 0" in narrow
    assert output_lines[-1] not in narrow
    assert output_lines[-1] not in wide
    assert "… +3 lines" in narrow
    assert "… +3 lines" in wide
    assert fragments_text(document.transcript_fragments(width=80)) == transcript
    assert command in transcript
    assert output_lines[-1] in transcript
