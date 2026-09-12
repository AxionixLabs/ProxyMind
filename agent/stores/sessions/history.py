# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ntpath
import os
import re
import sqlite3
import time
import typing
from pathlib import Path

from agent.domain.workspaces import workspace_path_key
from agent.protocol.context_usage import ContextUsageRecord
from protocol.schema.identifiers import valid_session_ids

TABLE_SESSION_CURSORS = "conversation_session_cursors"
TABLE_PENDING_FORKS = "conversation_pending_forks"
TABLE_CONTEXT_USAGE = "conversation_context_usage"

HISTORY_TTL_MS = 24 * 60 * 60 * 1000
HISTORY_LIMIT = 200
TITLE_MAX_CHARS = 500
HISTORY_MENU_LIMIT = 10

INTERACTIVE_HISTORY_SOURCES = (
    "review",
    "tui",
    "tui:resume",
)

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_SESSION_CURSORS} (
    cid            TEXT NOT NULL,
    sid            TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    workspace      TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    branch         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    expires_at     INTEGER NOT NULL,
    PRIMARY KEY (cid, sid)
);

CREATE INDEX IF NOT EXISTS idx_conversation_session_cursors_expires
ON {TABLE_SESSION_CURSORS} (expires_at);

CREATE INDEX IF NOT EXISTS idx_conversation_session_cursors_updated
ON {TABLE_SESSION_CURSORS} (updated_at DESC);

CREATE TABLE IF NOT EXISTS {TABLE_CONTEXT_USAGE} (
    cid TEXT NOT NULL,
    sid TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    event_seq INTEGER NOT NULL,
    presentation_epoch INTEGER NOT NULL,
    model_context_window INTEGER,
    last_total_tokens INTEGER,
    total_tokens INTEGER,
    usage_source TEXT NOT NULL,
    model TEXT NOT NULL,
    route TEXT NOT NULL,
    PRIMARY KEY (cid, sid),
    FOREIGN KEY (cid, sid) REFERENCES {TABLE_SESSION_CURSORS} (cid, sid)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS {TABLE_PENDING_FORKS} (
    cid         TEXT NOT NULL,
    sid         TEXT NOT NULL,
    before_turn_id TEXT NOT NULL DEFAULT '',
    request_id  TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    PRIMARY KEY (cid, sid)
);

CREATE INDEX IF NOT EXISTS idx_conversation_pending_forks_expires
ON {TABLE_PENDING_FORKS} (expires_at);
"""


class ConversationHistoryStore(object):
    """本地对话历史游标存储。"""

    def __init__(
        self,
        db_path: str | Path,
        *,
        ttl_ms: int = HISTORY_TTL_MS,
        max_items: int = HISTORY_LIMIT
    ) -> None:
        if not str(db_path or "").strip():
            raise ValueError("history db path is required")
        self.db_path = Path(db_path).expanduser()
        self.ttl_ms = max(1, int(ttl_ms or HISTORY_TTL_MS))
        self.max_items = max(1, int(max_items or HISTORY_LIMIT))

    def load_context_usage(self, cid: str, sid: str) -> ContextUsageRecord | None:
        """读取历史游标有效期内的完整远端用量缓存。"""
        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                self._prune_expired(conn, now_ms=_now_ms())
                row = conn.execute(
                    f"SELECT * FROM {TABLE_CONTEXT_USAGE} WHERE cid = ? AND sid = ?",
                    (cid, sid),
                ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ContextUsageRecord(
            cid=row["cid"],
            sid=row["sid"],
            turn_id=row["turn_id"],
            event_seq=row["event_seq"],
            presentation_epoch=row["presentation_epoch"],
            model_context_window=row["model_context_window"],
            last_total_tokens=row["last_total_tokens"],
            total_tokens=row["total_tokens"],
            usage_source=row["usage_source"],
            model=row["model"],
            route=row["route"],
        )

    def save_context_usage(self, record: ContextUsageRecord) -> bool:
        """按远端事件序号原子替换缓存，随父历史游标删除。"""
        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                cursor = conn.execute(
                    f"""
                    INSERT INTO {TABLE_CONTEXT_USAGE} (
                        cid, sid, turn_id, event_seq, presentation_epoch,
                        model_context_window, last_total_tokens, total_tokens,
                        usage_source, model, route
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cid, sid) DO UPDATE SET
                        turn_id = excluded.turn_id,
                        event_seq = excluded.event_seq,
                        presentation_epoch = excluded.presentation_epoch,
                        model_context_window = excluded.model_context_window,
                        last_total_tokens = excluded.last_total_tokens,
                        total_tokens = excluded.total_tokens,
                        usage_source = excluded.usage_source,
                        model = excluded.model,
                        route = excluded.route
                    WHERE excluded.event_seq > {TABLE_CONTEXT_USAGE}.event_seq
                    """,
                    (
                        record.cid, record.sid, record.turn_id, record.event_seq,
                        record.presentation_epoch, record.model_context_window,
                        record.last_total_tokens, record.total_tokens,
                        record.usage_source, record.model, record.route,
                    ),
                )
                return cursor.rowcount == 1
        finally:
            conn.close()

    def discard_context_usage_prefix(self, cid: str, sid: str, event_seq: int) -> None:
        """仅删除指定会话中落在已裁剪历史区间的旧用量缓存。"""
        if isinstance(event_seq, bool) or not isinstance(event_seq, int) or event_seq < 0:
            raise ValueError("context usage retention floor must be non-negative")
        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                conn.execute(
                    f"DELETE FROM {TABLE_CONTEXT_USAGE} WHERE cid = ? AND sid = ? AND event_seq <= ?",
                    (cid, sid, event_seq),
                )
        finally:
            conn.close()

    def touch_session(
        self,
        *,
        cid: str,
        sid: str,
        title: str = "",
        workspace: str = "",
        source: str = "",
        branch: str = "",
        status: str = "active",
        now_ms: typing.Optional[int] = None
    ) -> dict[str, typing.Any]:
        """记录最近出现过的 cid/sid；不读取消息内容。"""
        cid_text = _clean(cid)
        sid_text = _clean(sid)

        if not valid_session_ids(cid_text, sid_text):
            raise ValueError("valid cid and sid are required")

        now = _now_ms() if now_ms is None else int(now_ms)

        record = {
            "cid": cid_text,
            "sid": sid_text,
            "title": _clean_title(title),
            "workspace": normalize_workspace(workspace),
            "source": _clean(source),
            "branch": _clean(branch),
            "status": _normalize_status(status),
            "updated_at": now,
            "expires_at": now + self.ttl_ms
        }

        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                self._prune_expired(conn, now_ms=now)
                conn.execute(
                    f"""
                    INSERT INTO {TABLE_SESSION_CURSORS} (
                        cid, sid, workspace, source, branch, status,
                        title, created_at, updated_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cid, sid) DO UPDATE SET
                        workspace      = excluded.workspace,
                        source         = CASE
                            WHEN {TABLE_SESSION_CURSORS}.source = ''
                                 AND excluded.source <> ''
                            THEN excluded.source
                            ELSE {TABLE_SESSION_CURSORS}.source
                        END,
                        branch         = CASE
                            WHEN {TABLE_SESSION_CURSORS}.branch = ''
                                 AND excluded.branch <> ''
                            THEN excluded.branch
                            ELSE {TABLE_SESSION_CURSORS}.branch
                        END,
                        status         = CASE
                            WHEN {TABLE_SESSION_CURSORS}.status = 'archived'
                                 AND excluded.status <> 'archived'
                            THEN 'archived'
                            ELSE excluded.status
                        END,
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
                        record["workspace"],
                        record["source"],
                        record["branch"],
                        record["status"],
                        record["title"],
                        now,
                        record["updated_at"],
                        record["expires_at"]
                    )
                )
                self._trim(conn)
                row = conn.execute(
                    f"""
                    SELECT cid, sid, workspace, source,
                           title, created_at, updated_at, expires_at,
                           branch, status
                    FROM {TABLE_SESSION_CURSORS}
                    WHERE cid = ? AND sid = ?
                    """,
                    (record["cid"], record["sid"]),
                ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise sqlite3.DatabaseError("session was not persisted")
        return _row_to_dict(row)

    def archive_session(
        self,
        *,
        cid: str,
        sid: str,
        now_ms: typing.Optional[int] = None,
    ) -> dict[str, typing.Any]:
        """把已有会话原子迁移到 archived 集合。"""
        return self._set_session_status(
            cid=cid,
            sid=sid,
            status="archived",
            now_ms=now_ms,
        )

    def rename_session(
        self,
        *,
        cid: str,
        sid: str,
        title: str,
        now_ms: typing.Optional[int] = None,
    ) -> dict[str, typing.Any]:
        """原子更新已有会话的标题和恢复游标有效期。"""
        cid_text = _clean(cid)
        sid_text = _clean(sid)
        if not valid_session_ids(cid_text, sid_text):
            raise ValueError("valid cid and sid are required")

        normalized_title = " ".join(str(title or "").split())
        if len(normalized_title) > TITLE_MAX_CHARS:
            raise ValueError(
                f"session title must contain at most {TITLE_MAX_CHARS} characters"
            )
        title_text = _clean_title(normalized_title)
        if not title_text:
            raise ValueError("session title is required")

        now = _now_ms() if now_ms is None else int(now_ms)
        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                self._prune_expired(conn, now_ms=now)
                cursor = conn.execute(
                    f"""
                    UPDATE {TABLE_SESSION_CURSORS}
                    SET title = ?, updated_at = ?, expires_at = ?
                    WHERE cid = ? AND sid = ?
                    """,
                    (
                        title_text,
                        now,
                        now + self.ttl_ms,
                        cid_text,
                        sid_text,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LookupError("conversation session was not found")
                row = conn.execute(
                    f"""
                    SELECT cid, sid, workspace, source,
                           title, created_at, updated_at, expires_at,
                           branch, status
                    FROM {TABLE_SESSION_CURSORS}
                    WHERE cid = ? AND sid = ?
                    """,
                    (cid_text, sid_text),
                ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise sqlite3.DatabaseError("session title was not persisted")
        return _row_to_dict(row)

    def unarchive_session(
        self,
        *,
        cid: str,
        sid: str,
        now_ms: typing.Optional[int] = None,
    ) -> dict[str, typing.Any]:
        """把已有会话原子迁移回 active 集合。"""
        return self._set_session_status(
            cid=cid,
            sid=sid,
            status="active",
            now_ms=now_ms,
        )

    def list_sessions(
        self,
        *,
        workspace: str | Path | None = None,
        sources: typing.Collection[str] | None = None,
        status: str | None = None,
        limit: int = HISTORY_MENU_LIMIT,
        now_ms: typing.Optional[int] = None
    ) -> list[dict[str, typing.Any]]:
        """返回符合工作区和来源条件的最近会话游标。"""
        return self._select_sessions(
            workspace=workspace,
            sources=sources,
            status=status,
            limit=limit,
            now_ms=now_ms,
        )

    def find_session(
        self,
        session_id: str,
        *,
        workspace: str | Path | None = None,
        sources: typing.Collection[str] | None = None,
        status: str | None = None,
        now_ms: typing.Optional[int] = None
    ) -> dict[str, typing.Any] | None:
        """按会话标识查找一个未过期的会话游标。"""
        sid = _clean(session_id)
        if not sid:
            return None

        records = self._select_sessions(
            session_id=sid,
            workspace=workspace,
            sources=sources,
            status=status,
            limit=1,
            now_ms=now_ms,
        )
        return records[0] if records else None

    def get_or_create_fork_request(
        self,
        *,
        cid: str,
        sid: str,
        request_id: str,
        before_turn_id: str = "",
        now_ms: typing.Optional[int] = None
    ) -> str:
        """返回源会话尚未完成的稳定分支请求标识。"""
        cid_text = _clean(cid)
        sid_text = _clean(sid)
        candidate = _clean(request_id)
        boundary = _clean(before_turn_id)

        if not candidate or not valid_session_ids(cid_text, sid_text):
            raise ValueError("valid cid/sid and request_id are required")

        now = _now_ms() if now_ms is None else int(now_ms)

        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                conn.execute(
                    f"DELETE FROM {TABLE_PENDING_FORKS} WHERE expires_at <= ?",
                    (now,),
                )
                conn.execute(
                    f"""
                    DELETE FROM {TABLE_PENDING_FORKS}
                    WHERE cid = ? AND sid = ?
                      AND before_turn_id <> ?
                    """,
                    (cid_text, sid_text, boundary),
                )
                conn.execute(
                    f"""
                    INSERT INTO {TABLE_PENDING_FORKS} (
                        cid, sid, before_turn_id,
                        request_id, created_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cid, sid) DO NOTHING
                    """,
                    (
                        cid_text,
                        sid_text,
                        boundary,
                        candidate,
                        now,
                        now + self.ttl_ms,
                    ),
                )
                row = conn.execute(
                    f"""
                    SELECT request_id
                    FROM {TABLE_PENDING_FORKS}
                    WHERE cid = ? AND sid = ?
                      AND before_turn_id = ?
                    """,
                    (cid_text, sid_text, boundary),
                ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise sqlite3.DatabaseError("pending fork request was not persisted")
        return str(row["request_id"])

    def clear_fork_request(
        self,
        *,
        cid: str,
        sid: str,
        request_id: str,
        before_turn_id: str = ""
    ) -> None:
        """清除与指定源会话和请求标识匹配的分支操作。"""
        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                conn.execute(
                    f"""
                    DELETE FROM {TABLE_PENDING_FORKS}
                    WHERE cid = ? AND sid = ?
                      AND before_turn_id = ? AND request_id = ?
                    """,
                    (
                        _clean(cid),
                        _clean(sid),
                        _clean(before_turn_id),
                        _clean(request_id),
                    ),
                )
        finally:
            conn.close()

    def _select_sessions(
        self,
        *,
        session_id: str | None = None,
        workspace: str | Path | None,
        sources: typing.Collection[str] | None,
        status: str | None,
        limit: int,
        now_ms: typing.Optional[int]
    ) -> list[dict[str, typing.Any]]:
        """执行共享的会话游标查询。"""
        now = _now_ms() if now_ms is None else int(now_ms)
        item_limit = max(1, int(limit or HISTORY_MENU_LIMIT))

        clauses = ["expires_at > ?"]

        params: list[typing.Any] = [now]

        if session_id is not None:
            clauses.append("sid = ?")
            params.append(session_id)

        if workspace is not None:
            clauses.append("workspace_identity(workspace) = ?")
            params.append(workspace_identity(workspace))

        if status is not None:
            clauses.append("status = ?")
            params.append(_normalize_status(status))

        if sources is not None:
            source_values_list: list[str] = []
            for source in sources:
                value = _clean(source)
                if value and value not in source_values_list:
                    source_values_list.append(value)

            source_values = tuple(source_values_list)
            if not source_values:
                return []

            placeholders = ", ".join("?" for _ in source_values)
            clauses.append(f"source IN ({placeholders})")
            params.extend(source_values)

        conn = self._connect()
        try:
            with conn:
                self._init_schema(conn)
                self._prune_expired(conn, now_ms=now)
                rows = conn.execute(
                    f"""
                    SELECT cid, sid, workspace, source,
                           title, created_at, updated_at, expires_at,
                           branch, status
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

    def _set_session_status(
        self,
        *,
        cid: str,
        sid: str,
        status: str,
        now_ms: typing.Optional[int],
    ) -> dict[str, typing.Any]:
        """在同一事务中校验、迁移并返回会话状态。"""
        cid_text = _clean(cid)
        sid_text = _clean(sid)
        if not valid_session_ids(cid_text, sid_text):
            raise ValueError("valid cid and sid are required")

        target_status = _normalize_status(status)

        now = _now_ms() if now_ms is None else int(now_ms)
        conn = self._connect()

        try:
            with conn:
                self._init_schema(conn)
                self._prune_expired(conn, now_ms=now)
                row = conn.execute(
                    f"""
                    SELECT cid, sid, workspace, source,
                           title, created_at, updated_at, expires_at,
                           branch, status
                    FROM {TABLE_SESSION_CURSORS}
                    WHERE cid = ? AND sid = ?
                    """,
                    (cid_text, sid_text),
                ).fetchone()
                if row is None:
                    raise LookupError("conversation session was not found")

                conn.execute(
                    f"""
                    UPDATE {TABLE_SESSION_CURSORS}
                    SET status = ?, updated_at = ?, expires_at = ?
                    WHERE cid = ? AND sid = ?
                    """,
                    (
                        target_status,
                        now,
                        now + self.ttl_ms,
                        cid_text,
                        sid_text,
                    ),
                )
                updated = conn.execute(
                    f"""
                    SELECT cid, sid, workspace, source,
                           title, created_at, updated_at, expires_at,
                           branch, status
                    FROM {TABLE_SESSION_CURSORS}
                    WHERE cid = ? AND sid = ?
                    """,
                    (cid_text, sid_text),
                ).fetchone()
        finally:
            conn.close()

        if updated is None:
            raise sqlite3.DatabaseError("session status was not persisted")
        return _row_to_dict(updated)

    def _connect(self) -> sqlite3.Connection:
        """建立历史库连接。"""
        os.makedirs(self.db_path.parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        conn.create_function("workspace_identity", 1, workspace_identity)
        return conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        """初始化历史库结构。"""
        conn.executescript(SCHEMA_SQL)
        columns = {
            str(row[1])
            for row in conn.execute(
                f"PRAGMA table_info({TABLE_PENDING_FORKS})"
            )
        }
        if "mode" in columns:
            conn.execute(f"DROP TABLE {TABLE_PENDING_FORKS}")
            conn.executescript(SCHEMA_SQL)
            columns = {
                str(row[1])
                for row in conn.execute(
                    f"PRAGMA table_info({TABLE_PENDING_FORKS})"
                )
            }
        if "before_turn_id" not in columns:
            conn.execute(
                f"""
                ALTER TABLE {TABLE_PENDING_FORKS}
                ADD COLUMN before_turn_id TEXT NOT NULL DEFAULT ''
                """
            )

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


def normalize_workspace(workspace: typing.Any) -> str:
    """返回跨平台稳定的工作区路径；空工作区返回空字符串。"""
    workspace_text = _clean(workspace)
    if not workspace_text:
        return ""

    normalized = workspace_text
    if os.name == "nt" or not _looks_like_windows_path(workspace_text):
        try:
            normalized = str(Path(workspace_text).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            pass

    if _looks_like_windows_path(normalized):
        normalized = normalized.replace("/", "\\")
        if normalized.startswith("\\\\?\\UNC\\"):
            normalized = "\\\\" + normalized[8:]
        elif normalized.startswith("\\\\?\\"):
            normalized = normalized[4:]
        normalized = ntpath.normpath(normalized)
    normalized = normalized.replace("\\", "/").rstrip("/") or "/"
    if len(normalized) == 2 and normalized[1] == ":":
        normalized += "/"

    if re.match(r"^[a-zA-Z]:/", normalized):
        normalized = normalized[0].lower() + normalized[1:]

    return normalized


def workspace_identity(workspace: str | os.PathLike[str] | None) -> str:
    """返回目录比较键，保留 POSIX 大小写并统一 Windows 路径身份。"""
    normalized = normalize_workspace(workspace)
    return workspace_path_key(normalized)


def _looks_like_windows_path(value: str) -> bool:
    """判断文本是否是 Windows drive 或 UNC 路径。"""
    return bool(
        re.match(r"^[a-zA-Z]:[\\/]", value)
        or re.match(r"^[a-zA-Z]:$", value)
        or value.startswith("\\\\")
        or value.startswith("//")
    )


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


def _normalize_status(value: typing.Any) -> str:
    """把历史状态限制为 picker 可识别的活动或归档值。"""
    status = _clean(value).casefold()
    return "archived" if status == "archived" else "active"


if __name__ == '__main__':
    pass
