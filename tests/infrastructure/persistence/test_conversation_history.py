# -*- coding: utf-8 -*-

import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.domain.transcripts import TranscriptEntry
from agent.protocol.context_usage import ContextUsageRecord
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


def test_context_cache_is_monotonic_and_survives_reopening(tmp_path) -> None:
    path = tmp_path / "history.db"
    store = ConversationHistoryStore(path)
    record = ContextUsageRecord(
        cid="cid_alpha_12345678", sid="sid_alpha_1_abcdef", turn_id="turn_1",
        event_seq=12, presentation_epoch=1, model_context_window=100_000,
        last_total_tokens=20_000, total_tokens=250_000, usage_source="provider",
        model="test-model", route="responses",
    )
    store.touch_session(cid=record.cid, sid=record.sid)
    assert store.save_context_usage(record)
    assert not store.save_context_usage(replace(record, event_seq=10, total_tokens=1))
    assert not store.save_context_usage(record)
    reopened = ConversationHistoryStore(path)
    assert reopened.load_context_usage(record.cid, record.sid) == record
    assert reopened.load_context_usage(record.cid, "other") is None
    compacted = replace(record, event_seq=20, last_total_tokens=13_000, usage_source="estimate")
    assert reopened.save_context_usage(compacted)
    assert store.load_context_usage(record.cid, record.sid) == compacted
    reopened.discard_context_usage_prefix(record.cid, record.sid, 19)
    assert store.load_context_usage(record.cid, record.sid) == compacted
    reopened.discard_context_usage_prefix(record.cid, record.sid, 20)
    assert store.load_context_usage(record.cid, record.sid) is None


def test_context_cache_is_pruned_with_expired_and_trimmed_history(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", max_items=1)
    record = ContextUsageRecord(
        cid="cid_alpha_12345678", sid="sid_alpha_1_abcdef", turn_id="turn_1",
        event_seq=12, presentation_epoch=1, model_context_window=100_000,
        last_total_tokens=20_000, total_tokens=None, usage_source="estimate",
        model="test-model", route="responses",
    )
    first = store.touch_session(cid=record.cid, sid=record.sid)
    store.save_context_usage(record)
    store.touch_session(
        cid="cid_beta_12345678", sid="sid_beta_1_abcdef", now_ms=first["updated_at"] + 1,
    )
    assert store.load_context_usage(record.cid, record.sid) is None
    expired = replace(record, cid="cid_old_12345678", sid="sid_old_1_abcdef")
    store = ConversationHistoryStore(tmp_path / "expired.db")
    store.touch_session(cid=expired.cid, sid=expired.sid, now_ms=0)
    store.save_context_usage(expired)
    assert store.load_context_usage(expired.cid, expired.sid) is None


def test_context_cache_failure_is_explicit_unknown() -> None:
    history = LocalConversationHistory(
        SimpleNamespace(load_context_usage=Mock(side_effect=sqlite3.DatabaseError("unavailable"))),
        existing_transcript_path_for=lambda _sid: "",
        transcript_entries_for=lambda _path: (),
    )
    assert history.load_context_usage("cid", "sid") is None
