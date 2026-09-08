# -*- coding: utf-8 -*-

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.domain.transcripts import TranscriptEntry
from agent.stores.sessions import ConversationHistoryStore
from infrastructure.persistence.conversation_history import (
    ConversationForkPersistenceError,
    LocalConversationHistory,
)


def test_local_conversation_history_composes_cursor_and_transcript_storage(
    tmp_path,
) -> None:
    """本地历史适配器组合游标、分支幂等和 Transcript 读取。"""
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
    entry = TranscriptEntry(
        timestamp="2026-09-02T00:00:00.000Z",
        event="message.created",
        session_id="sid_alpha_1_abcdef",
        turn_id="turn_alpha",
        actor="user",
        payload={"content": "inspect"},
    )
    history = LocalConversationHistory(
        store,
        existing_transcript_path_for=lambda sid: f"{sid}.jsonl",
        transcript_entries_for=lambda _path: (entry,),
    )
    coordinates = {
        "cid": "cid_alpha_12345678",
        "sid": "sid_alpha_1_abcdef",
    }

    history.touch(
        coordinates,
        workspace=str(tmp_path),
        title="Inspect",
        source="tui",
    )
    assert history.update_title(
        coordinates["cid"],
        coordinates["sid"],
        "Review current changes",
    )

    record = history.find(coordinates["sid"])
    assert record is not None
    assert record["title"] == "Review current changes"
    assert [record["sid"] for record in history.recent()] == [
        coordinates["sid"],
    ]
    assert history.read_transcript(coordinates["sid"]) == (entry,)

    request_id = history.prepare_fork(**coordinates)
    assert history.prepare_fork(**coordinates) == request_id
    history.clear_fork(**coordinates, request_id=request_id)
    assert history.prepare_fork(**coordinates) != request_id


def test_local_conversation_history_contains_storage_read_failures() -> None:
    """游标读写故障在基础设施边界降级，分支登记失败保持显式。"""
    failure = sqlite3.DatabaseError("history unavailable")
    store = SimpleNamespace(
        ttl_ms=10_000,
        max_items=200,
        touch_session=Mock(side_effect=failure),
        rename_session=Mock(side_effect=failure),
        list_sessions=Mock(side_effect=failure),
        find_session=Mock(side_effect=failure),
        get_or_create_fork_request=Mock(side_effect=failure),
    )
    history = LocalConversationHistory(
        store,
        existing_transcript_path_for=lambda _sid: "",
        transcript_entries_for=lambda _path: (),
    )
    coordinates = {
        "cid": "cid_alpha_12345678",
        "sid": "sid_alpha_1_abcdef",
    }

    history.touch(coordinates, workspace="D:/workspace", source="tui")

    assert not history.update_title(
        coordinates["cid"],
        coordinates["sid"],
        "Review current changes",
    )
    assert history.recent() == []
    assert history.find(coordinates["sid"]) is None
    with pytest.raises(
        ConversationForkPersistenceError,
        match="Unable to persist",
    ):
        history.prepare_fork(**coordinates)
