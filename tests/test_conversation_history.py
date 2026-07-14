# -*- coding: utf-8 -*-

import sqlite3
from pathlib import Path

from mind_app.history.store import ConversationHistoryStore


def test_history_store_uses_one_session_list_without_mode_partition(tmp_path: Path) -> None:
    """对话历史不按模式分层，并且存储结构不包含模式字段。"""
    db_path = tmp_path / "history.sqlite3"
    store = ConversationHistoryStore(db_path)

    store.touch_session(
        cid="cid_chat_1234abcd",
        sid="sid_chat_first_abcdef",
        title="first conversation",
        workspace="D:/workspace",
        gravity="default",
        source="repl",
        now_ms=100
    )
    store.touch_session(
        cid="cid_fast_1234abcd",
        sid="sid_fast_second_abcdef",
        title="second conversation",
        workspace="D:/workspace",
        gravity="default",
        source="repl",
        now_ms=200
    )

    records = store.list_sessions(
        workspace="D:/workspace",
        gravity="default",
        now_ms=300
    )

    assert [record["cid"] for record in records] == [
        "cid_fast_1234abcd",
        "cid_chat_1234abcd"
    ]

    conn = sqlite3.connect(db_path)
    try:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(conversation_session_cursors)")
        }
    finally:
        conn.close()

    assert "mode" not in columns
