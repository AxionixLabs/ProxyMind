# -*- coding: utf-8 -*-

import json
from datetime import datetime
from pathlib import Path

from mind_app.history.transcript import ConversationTranscriptStore
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
