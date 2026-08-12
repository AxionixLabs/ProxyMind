# -*- coding: utf-8 -*-

import json
from datetime import datetime
from pathlib import Path

from mind_app.history.transcript import (
    ConversationTranscriptStore,
    TranscriptEntry,
    TranscriptReader,
    TranscriptReplay,
    TranscriptWriter,
)
from mind_app.history.contracts import TranscriptSink
from mind_nova.identifiers import new_cid, new_sid


def test_store_uses_session_creation_date_and_stable_path(tmp_path) -> None:
    session_id = new_sid(new_cid())
    store = ConversationTranscriptStore(tmp_path / "sessions")

    first = Path(store.path_for_session(session_id))
    second = Path(store.path_for_session(session_id))

    created_at = datetime.fromtimestamp(
        int(session_id.split("_")[2], 36) / 1000
    ).astimezone()
    expected_parent = (
        tmp_path
        / "sessions"
        / created_at.strftime("%Y")
        / created_at.strftime("%m")
        / created_at.strftime("%d")
    )

    assert first == second
    assert first.parent == expected_parent
    assert first.name == f"session-{session_id}.jsonl"
    assert first.exists()


def test_writer_appends_complete_events_without_schema_version(tmp_path) -> None:
    session_id = new_sid(new_cid())
    store = ConversationTranscriptStore(tmp_path / "sessions")
    path = store.path_for_session(session_id)
    writer = store.writer(
        path,
        session_id=session_id,
        turn_id="turn_test",
    )

    writer.open()
    writer.append(
        "message.created",
        actor="user",
        payload={"content": "hello"},
    )
    writer.append(
        "tool.completed",
        actor="tool",
        payload={"ok": True, "score": float("nan")},
    )
    writer.close()

    entries = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
    ]

    assert [entry["event"] for entry in entries] == [
        "message.created",
        "tool.completed",
    ]
    assert entries[0]["session_id"] == session_id
    assert entries[0]["turn_id"] == "turn_test"
    assert entries[0]["actor"] == "user"
    assert entries[0]["payload"] == {"content": "hello"}
    assert entries[1]["payload"]["score"] == "nan"
    assert all("version" not in entry for entry in entries)
    assert all("schema_version" not in entry for entry in entries)


def test_writer_implements_transcript_sink_contract() -> None:
    writer = TranscriptWriter("", session_id="session_test")

    assert isinstance(writer, TranscriptSink)


def test_reader_returns_written_entries(tmp_path) -> None:
    session_id = new_sid(new_cid())
    store = ConversationTranscriptStore(tmp_path / "sessions")
    path = store.path_for_session(session_id)
    writer = store.writer(
        path,
        session_id=session_id,
        turn_id="turn_test",
    )

    writer.open()
    writer.append(
        "message.created",
        actor="assistant",
        payload={"content": "done"},
    )
    writer.close()

    entries = store.reader(path).read()

    assert len(entries) == 1
    assert entries[0].event == "message.created"
    assert entries[0].session_id == session_id
    assert entries[0].turn_id == "turn_test"
    assert entries[0].actor == "assistant"
    assert entries[0].payload == {"content": "done"}


def test_reader_tail_returns_only_latest_events(tmp_path) -> None:
    session_id = new_sid(new_cid())
    path = tmp_path / "session.jsonl"
    writer = TranscriptWriter(
        path,
        session_id=session_id,
        turn_id="turn_test",
    )
    writer.open()
    for index in range(80):
        writer.append(
            "message.created",
            actor="assistant",
            payload={"content": f"事件 {index} " + "x" * 180},
        )
    writer.close()

    entries = TranscriptReader(path).read_tail(3)

    assert [entry.payload["content"].split()[1] for entry in entries] == [
        "77",
        "78",
        "79",
    ]


def test_reader_skips_damaged_and_invalid_lines(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    valid = {
        "timestamp": "2026-08-02T00:00:00.000Z",
        "event": "future.event",
        "session_id": "session_test",
        "turn_id": None,
        "actor": "system",
        "payload": {"value": 1},
    }
    invalid = {**valid, "payload": "invalid"}
    path.write_text(
        "\n".join([
            "{\"event\":",
            json.dumps(invalid),
            json.dumps(valid),
        ]),
        encoding="utf-8",
    )

    assert ConversationTranscriptStore.reader(path).read() == (
        TranscriptEntry.from_dict(valid),
    )


def test_replay_merges_message_updates_and_tool_outcomes() -> None:
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

    replay = TranscriptReplay((
        entry(
            "message.created",
            actor="user",
            payload={"content": "original"},
        ),
        entry(
            "message.updated",
            actor="user",
            payload={"content": "canonical"},
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
                "ok": True,
                "result": {"output": "workspace"},
            },
        ),
        entry("future.event", actor="system", payload={}),
    )).build()

    assert [item.event for item in replay] == [
        "message.created",
        "tool.completed",
    ]
    assert replay[0].payload["content"] == "canonical"
    assert replay[1].payload == {
        "call_id": "call_1",
        "name": "shell_command",
        "arguments": {"command": "pwd"},
        "ok": True,
        "result": {"output": "workspace"},
    }


def test_replay_keeps_javascript_start_and_result_separate() -> None:
    def entry(event: str, payload: dict) -> TranscriptEntry:
        return TranscriptEntry(
            timestamp="2026-08-02T00:00:00.000Z",
            event=event,
            session_id="session_test",
            turn_id="turn_test",
            actor="tool",
            payload=payload,
        )

    replay = TranscriptReplay((
        entry(
            "tool.started",
            {
                "call_id": "call_js",
                "name": "js_repl",
                "arguments": {"code": "console.log('ready');"},
            },
        ),
        entry(
            "tool.completed",
            {
                "call_id": "call_js",
                "ok": True,
                "result": {"output": "ready"},
            },
        ),
    )).build()

    assert [item.event for item in replay] == [
        "tool.started",
        "tool.completed",
    ]


def test_store_finds_only_existing_transcript_path(tmp_path) -> None:
    session_id = new_sid(new_cid())
    store = ConversationTranscriptStore(tmp_path / "sessions")

    assert store.existing_path_for_session(session_id) == ""

    path = store.path_for_session(session_id)

    assert store.existing_path_for_session(session_id) == path
