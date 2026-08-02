# -*- coding: utf-8 -*-

import sqlite3

from mind_app.history import (
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

    assert [record["sid"] for record in records] == [interactive["sid"]]
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
