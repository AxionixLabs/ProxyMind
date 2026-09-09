# -*- coding: utf-8 -*-

import sqlite3

import pytest

from agent.stores.sessions import (
    ConversationHistoryStore,
    INTERACTIVE_HISTORY_SOURCES,
)


def test_history_queries_filter_workspace_source_and_session_id(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)

    interactive = store.touch_session(
        cid="cid_alpha_12345678",
        sid="sid_alpha_1_abcdef",
        workspace=r"D:\workspace\alpha",
        source="tui",
        now_ms=100,
    )
    review = store.touch_session(
        cid="cid_review_12345678",
        sid="sid_review_1_abcdef",
        workspace=r"D:\workspace\alpha",
        source="review",
        now_ms=150,
    )
    store.touch_session(
        cid="cid_beta_12345678",
        sid="sid_beta_1_abcdef",
        workspace=r"D:\workspace\beta",
        source="calling",
        now_ms=200,
    )

    records = store.list_sessions(
        workspace=r"D:\workspace\alpha",
        sources=INTERACTIVE_HISTORY_SOURCES,
        now_ms=300,
    )
    found = store.find_session(
        interactive["sid"],
        workspace=r"D:\workspace\alpha",
        sources=INTERACTIVE_HISTORY_SOURCES,
        now_ms=300,
    )

    assert [record["sid"] for record in records] == [
        review["sid"],
        interactive["sid"],
    ]
    assert found is not None
    assert found["cid"] == interactive["cid"]


def test_history_keeps_the_original_session_source(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
    metadata = {
        "cid": "cid_alpha_12345678",
        "sid": "sid_alpha_1_abcdef",
    }

    store.touch_session(
        **metadata,
        source="tui",
        now_ms=100,
    )
    store.touch_session(
        **metadata,
        source="mcp_server",
        now_ms=200,
    )

    record = store.find_session(metadata["sid"], now_ms=300)

    assert record is not None
    assert record["source"] == "tui"


def test_history_renames_only_an_existing_session(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
    session = {
        "cid": "cid_alpha_12345678",
        "sid": "sid_alpha_1_abcdef",
    }
    store.touch_session(
        **session,
        title="Initial title",
        workspace="D:/workspace",
        source="tui",
        now_ms=100,
    )

    renamed = store.rename_session(
        **session,
        title="Review current changes",
        now_ms=200,
    )

    assert renamed["title"] == "Review current changes"
    assert renamed["workspace"] == "d:/workspace"
    assert renamed["source"] == "tui"
    assert renamed["updated_at"] == 200
    assert renamed["expires_at"] == 10_200

    with pytest.raises(LookupError, match="was not found"):
        store.rename_session(
            cid="cid_missing_12345678",
            sid="sid_missing_1_abcdef",
            title="Must not create a cursor",
            now_ms=300,
        )

    for length in (81, 500):
        title = "审" * length
        store.rename_session(**session, title=title, now_ms=250)
        reopened = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
        record = reopened.find_session(session["sid"], now_ms=260)
        assert record is not None
        assert record["title"] == title

    with pytest.raises(ValueError, match="at most 500"):
        store.rename_session(
            **session,
            title="x" * 501,
            now_ms=300,
        )


def test_history_persists_branch_and_status(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)

    store.touch_session(
        cid="cid_branch_12345678",
        sid="sid_branch_1_abcdef",
        branch="feature/resume",
        status="archived",
        now_ms=100,
    )

    record = store.find_session("sid_branch_1_abcdef", now_ms=200)

    assert record is not None
    assert record["branch"] == "feature/resume"
    assert record["status"] == "archived"


def test_history_archive_and_unarchive_are_idempotent(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
    session = {
        "cid": "cid_archive_12345678",
        "sid": "sid_archive_1_abcdef",
    }

    store.touch_session(**session, title="archive me", now_ms=100)

    archived = store.archive_session(**session, now_ms=200)
    repeated_archive = store.archive_session(**session, now_ms=300)
    assert archived["status"] == "archived"
    assert repeated_archive["status"] == "archived"
    assert repeated_archive["title"] == "archive me"

    active = store.unarchive_session(**session, now_ms=400)
    repeated_unarchive = store.unarchive_session(**session, now_ms=500)
    assert active["status"] == "active"
    assert repeated_unarchive["status"] == "active"


def test_history_touch_does_not_restore_archived_session(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
    session = {
        "cid": "cid_archive_12345678",
        "sid": "sid_archive_1_abcdef",
    }

    store.touch_session(**session, now_ms=100)
    store.archive_session(**session, now_ms=200)

    touched = store.touch_session(**session, source="tui:resume", now_ms=300)
    assert touched["status"] == "archived"
    assert store.list_sessions(status="active", now_ms=301) == []
    assert [row["sid"] for row in store.list_sessions(
        status="archived",
        now_ms=301,
    )] == [session["sid"]]


def test_history_status_migration_requires_existing_session(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db")

    try:
        store.archive_session(
            cid="cid_missing_12345678",
            sid="sid_missing_1_abcdef",
        )
    except LookupError as error:
        assert str(error) == "conversation session was not found"
    else:
        raise AssertionError("missing sessions must not be implicitly created")


def test_history_reuses_pending_fork_request_until_cleared(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
    source = {
        "cid": "cid_alpha_12345678",
        "sid": "sid_alpha_1_abcdef",
    }

    first = store.get_or_create_fork_request(
        **source,
        request_id="fork_request_one",
        now_ms=100,
    )
    retried = store.get_or_create_fork_request(
        **source,
        request_id="fork_request_two",
        now_ms=200,
    )

    assert first == "fork_request_one"
    assert retried == first

    store.clear_fork_request(**source, request_id=first)
    next_request = store.get_or_create_fork_request(
        **source,
        request_id="fork_request_two",
        now_ms=300,
    )

    assert next_request == "fork_request_two"


def test_history_scopes_pending_fork_request_to_turn_boundary(tmp_path) -> None:
    store = ConversationHistoryStore(tmp_path / "history.db", ttl_ms=10_000)
    source = {
        "cid": "cid_alpha_12345678",
        "sid": "sid_alpha_1_abcdef",
    }

    first = store.get_or_create_fork_request(
        **source,
        request_id="fork_request_one",
        before_turn_id="turn_one",
        now_ms=100,
    )
    retried = store.get_or_create_fork_request(
        **source,
        request_id="fork_request_two",
        before_turn_id="turn_one",
        now_ms=200,
    )
    changed = store.get_or_create_fork_request(
        **source,
        request_id="fork_request_three",
        before_turn_id="turn_two",
        now_ms=300,
    )

    assert retried == first == "fork_request_one"
    assert changed == "fork_request_three"


def test_history_migrates_pending_fork_turn_boundary(tmp_path) -> None:
    db_path = tmp_path / "history.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE conversation_pending_forks (
                mode TEXT NOT NULL,
                cid TEXT NOT NULL,
                sid TEXT NOT NULL,
                request_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                PRIMARY KEY (mode, cid, sid)
            )
        """)

    store = ConversationHistoryStore(db_path, ttl_ms=10_000)
    request_id = store.get_or_create_fork_request(
        cid="cid_alpha_12345678",
        sid="sid_alpha_1_abcdef",
        request_id="fork_request_one",
        before_turn_id="turn_one",
        now_ms=100,
    )

    assert request_id == "fork_request_one"
    with sqlite3.connect(db_path) as conn:
        columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(conversation_pending_forks)"
            )
        }
    assert "before_turn_id" in columns
    assert "mode" not in columns
