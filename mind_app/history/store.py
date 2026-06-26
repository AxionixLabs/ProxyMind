# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import time
import typing
import hashlib
import sqlite3
from pathlib import Path
from mind_app.paths import mind_history_db_path
from .ids import valid_session_ids

TABLE_SESSION_CURSORS = "conversation_session_cursors"

HISTORY_TTL_MS     = 24 * 60 * 60 * 1000
HISTORY_LIMIT      = 200
TITLE_MAX_CHARS    = 80
HISTORY_MENU_LIMIT = 10

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SESSION_CURSORS} (
    cid            TEXT NOT NULL,
    sid            TEXT NOT NULL,
    mode           TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    workspace_hash TEXT NOT NULL DEFAULT '',
    gravity        TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    expires_at     INTEGER NOT NULL,
    PRIMARY KEY (cid, sid)
);

CREATE INDEX IF NOT EXISTS idx_conversation_session_cursors_expires
ON {TABLE_SESSION_CURSORS} (expires_at);

CREATE INDEX IF NOT EXISTS idx_conversation_session_cursors_updated
ON {TABLE_SESSION_CURSORS} (updated_at DESC);
"""


class ConversationHistoryStore(object):
    """本地对话历史游标存储。"""

    def __init__(
        self,
        db_path: typing.Optional[Path] = None,
        *,
        ttl_ms: int = HISTORY_TTL_MS,
        max_items: int = HISTORY_LIMIT
    ) -> None:
        self.db_path   = Path(db_path or mind_history_db_path()).expanduser()
        self.ttl_ms    = max(1, int(ttl_ms or HISTORY_TTL_MS))
        self.max_items = max(1, int(max_items or HISTORY_LIMIT))

    def touch_session(
        self,
        *,
        cid: str,
        sid: str,
        mode: str = "",
        title: str = "",
        workspace: str = "",
        gravity: str = "",
        source: str = "",
        now_ms: typing.Optional[int] = None
    ) -> dict[str, typing.Any]:
        """记录最近出现过的 cid/sid；不读取消息内容。"""
        cid_text = _clean(cid)
        sid_text = _clean(sid)

        if not valid_session_ids(cid_text, sid_text):
            raise ValueError("valid cid and sid are required")

        now = _now_ms() if now_ms is None else int(now_ms)

        record = {
            "cid"            : cid_text,
            "sid"            : sid_text,
            "mode"           : _clean(mode),
            "title"          : _clean_title(title),
            "workspace_hash" : workspace_hash(workspace),
            "gravity"        : _clean(gravity),
            "source"         : _clean(source),
            "updated_at"     : now,
            "expires_at"     : now + self.ttl_ms
        }

        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                self._prune_expired(conn, now_ms=now)
                conn.execute(
                    f"""
                    INSERT INTO {TABLE_SESSION_CURSORS} (
                        cid, sid, mode, workspace_hash, gravity, source,
                        title, created_at, updated_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cid, sid) DO UPDATE SET
                        mode           = excluded.mode,
                        workspace_hash = excluded.workspace_hash,
                        gravity        = excluded.gravity,
                        source         = excluded.source,
                        title          = CASE
                            WHEN {TABLE_SESSION_CURSORS}.title = ''
                                 AND excluded.title <> ''
                            THEN excluded.title
                            ELSE {TABLE_SESSION_CURSORS}.title
                        END,
                        updated_at     = excluded.updated_at,
                        expires_at     = excluded.expires_at
                    """,
                    (
                        record["cid"],
                        record["sid"],
                        record["mode"],
                        record["workspace_hash"],
                        record["gravity"],
                        record["source"],
                        record["title"],
                        now,
                        record["updated_at"],
                        record["expires_at"]
                    )
                )
                self._trim(conn)
        finally:
            conn.close()

        return record

    def list_sessions(
        self,
        *,
        mode: str = "",
        workspace: str = "",
        gravity: str = "",
        limit: int = HISTORY_MENU_LIMIT,
        now_ms: typing.Optional[int] = None
    ) -> list[dict[str, typing.Any]]:
        """返回当前 workspace/gravity 下最近未过期的会话游标列表。"""
        now = _now_ms() if now_ms is None else int(now_ms)

        mode_text     = _clean(mode)
        workspace_key = workspace_hash(workspace)
        gravity_text  = _clean(gravity)
        item_limit    = max(1, int(limit or HISTORY_MENU_LIMIT))

        clauses = ["expires_at > ?"]

        params: list[typing.Any] = [now]

        if workspace_key:
            clauses.append("workspace_hash = ?")
            params.append(workspace_key)

        if mode_text:
            clauses.append("mode = ?")
            params.append(mode_text)

        if gravity_text:
            clauses.append("gravity = ?")
            params.append(gravity_text)

        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                self._prune_expired(conn, now_ms=now)
                rows = conn.execute(
                    f"""
                    SELECT cid, sid, mode, workspace_hash, gravity, source,
                           title, created_at, updated_at, expires_at
                    FROM {TABLE_SESSION_CURSORS}
                    WHERE {" AND ".join(clauses)}
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    tuple(params + [item_limit])
                ).fetchall()
        finally:
            conn.close()

        return [_row_to_dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        """建立历史库连接。"""
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        """初始化历史库结构。"""
        conn.executescript(SCHEMA_SQL)

    @staticmethod
    def _prune_expired(conn: sqlite3.Connection, *, now_ms: int) -> None:
        """删除过期会话游标。"""
        conn.execute(
            f"DELETE FROM {TABLE_SESSION_CURSORS} WHERE expires_at <= ?",
            (int(now_ms),)
        )

    def _trim(self, conn: sqlite3.Connection) -> None:
        """限制 history 游标数量。"""
        conn.execute(
            f"""
            DELETE FROM {TABLE_SESSION_CURSORS}
            WHERE rowid NOT IN (
                SELECT rowid
                FROM {TABLE_SESSION_CURSORS}
                ORDER BY updated_at DESC
                LIMIT ?
            )
            """,
            (self.max_items,)
        )


def workspace_hash(workspace: typing.Any) -> str:
    """返回工作区路径的稳定短 hash；空工作区返回空字符串。"""
    workspace_text = _clean(workspace)
    if not workspace_text:
        return ""

    normalized = str(Path(workspace_text).expanduser()).replace("\\", "/").lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _row_to_dict(row: sqlite3.Row) -> dict[str, typing.Any]:
    """把 SQLite Row 转成普通字典。"""
    return {key: row[key] for key in row.keys()}


def _now_ms() -> int:
    """返回当前时间戳，单位为毫秒。"""
    return int(time.time() * 1000)


def _clean(value: typing.Any) -> str:
    """清理短文本字段。"""
    return str(value or "").strip()


def _clean_title(value: typing.Any) -> str:
    """把首条 query 压缩成短标题。"""
    text = " ".join(str(value or "").split())
    return text[:TITLE_MAX_CHARS]


if __name__ == '__main__':
    pass
