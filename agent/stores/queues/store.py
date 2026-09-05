# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import hashlib
import json
import sqlite3
import typing
from collections.abc import Mapping
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

from agent.ports.durable_queue import DurableQueuePersistenceConflict
from agent.protocol import (
    LocalDurableQueueSnapshot,
    ModelStreamRequest,
    SubmitTurnCommand,
)
from agent.protocol.json_value import ThawedJsonValue
from protocol.schema.identifiers import (
    normalize_request_id,
    normalize_submission_id,
    normalize_turn_id,
)
from .schema import (
    QUEUE_STORE_SCHEMA_SQL,
    QUEUE_STORE_SCHEMA_VERSION,
)


class SQLiteDurableQueueStore:
    """使用独立 SQLite 账本保存 Queue 的本地冻结执行快照。"""

    def __init__(self, db_path: str | Path) -> None:
        """绑定 Queue 专用数据库文件。"""
        self.db_path = Path(db_path).expanduser()

    async def create(
        self,
        command: SubmitTurnCommand,
        request: ModelStreamRequest,
        *,
        submission_id: str,
        client_message_id: str,
        add_request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """在远端 add 前事务保存不可变执行语义。"""
        return await asyncio.to_thread(
            self._create,
            command,
            request,
            submission_id,
            client_message_id,
            add_request_id,
        )

    async def find(
        self,
        submission_id: str,
    ) -> LocalDurableQueueSnapshot | None:
        """按 submission identity 读取本地执行快照。"""
        normalized = normalize_submission_id(submission_id)
        return await asyncio.to_thread(self._find, normalized)

    async def list_session(
        self,
        *,
        cid: str,
        sid: str,
    ) -> tuple[LocalDurableQueueSnapshot, ...]:
        """按创建顺序读取 Session 的本地执行快照。"""
        normalized_cid, normalized_sid = _session_coordinates(cid, sid)
        return await asyncio.to_thread(
            self._list_session,
            normalized_cid,
            normalized_sid,
        )

    async def mark_queued(
        self,
        submission_id: str,
        *,
        add_request_id: str,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """提交远端已接受 add 的本地投影。"""
        normalized_submission_id = normalize_submission_id(submission_id)
        normalized_request_id = normalize_request_id(add_request_id)
        _queue_version(queue_version)
        return await asyncio.to_thread(
            self._mark_queued,
            normalized_submission_id,
            normalized_request_id,
            queue_version,
        )

    async def begin_start(
        self,
        submission_id: str,
        *,
        request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """持久化一次尚未获得确定结果的 start 尝试。"""
        normalized_submission_id = normalize_submission_id(submission_id)
        normalized_request_id = normalize_request_id(request_id)
        return await asyncio.to_thread(
            self._begin_start,
            normalized_submission_id,
            normalized_request_id,
        )

    async def mark_started(
        self,
        submission_id: str,
        *,
        request_id: str,
        turn_id: str,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """提交 Queue item 已转换为既有 Turn 的本地投影。"""
        normalized_submission_id = normalize_submission_id(submission_id)
        normalized_request_id = normalize_request_id(request_id)
        normalized_turn_id = normalize_turn_id(turn_id)
        _queue_version(queue_version)
        return await asyncio.to_thread(
            self._mark_started,
            normalized_submission_id,
            normalized_request_id,
            normalized_turn_id,
            queue_version,
        )

    async def reset_start(
        self,
        submission_id: str,
        *,
        request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """在远端确定未启动时清除本次 start 尝试。"""
        normalized_submission_id = normalize_submission_id(submission_id)
        normalized_request_id = normalize_request_id(request_id)
        return await asyncio.to_thread(
            self._reset_start,
            normalized_submission_id,
            normalized_request_id,
        )

    async def mark_deleted(
        self,
        submission_id: str,
        *,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """提交服务端已删除 Queue item 的本地投影。"""
        normalized_submission_id = normalize_submission_id(submission_id)
        _queue_version(queue_version)
        return await asyncio.to_thread(
            self._mark_deleted,
            normalized_submission_id,
            queue_version,
        )

    async def mark_settled(
        self,
        submission_id: str,
    ) -> LocalDurableQueueSnapshot:
        """提交 Queue Turn 已收到权威终态的本地恢复事实。"""
        normalized_submission_id = normalize_submission_id(submission_id)
        return await asyncio.to_thread(
            self._mark_settled,
            normalized_submission_id,
        )

    def _connect(self) -> sqlite3.Connection:
        """建立启用 WAL 和 FULL 同步的独立 Queue 数据库连接。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        current_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if current_version > QUEUE_STORE_SCHEMA_VERSION:
            connection.close()
            raise RuntimeError("durable queue store schema is newer than this client")
        connection.executescript(QUEUE_STORE_SCHEMA_SQL)
        if current_version < QUEUE_STORE_SCHEMA_VERSION:
            connection.execute(
                f"PRAGMA user_version={QUEUE_STORE_SCHEMA_VERSION}"
            )
        return connection

    def _create(
        self,
        command: SubmitTurnCommand,
        request: ModelStreamRequest,
        submission_id: str,
        client_message_id: str,
        add_request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """同步创建或验证同一 Queue 本地快照。"""
        normalized_submission_id = normalize_submission_id(submission_id)
        normalized_add_request_id = normalize_request_id(add_request_id)
        normalized_client_message_id = _client_message_id(client_message_id)
        _validate_command_coordinates(command, request)
        command_json = _encode_json(command.to_dict())
        request_json = _encode_json(request.to_dict())
        fingerprint = _fingerprint(
            normalized_submission_id,
            normalized_client_message_id,
            normalized_add_request_id,
            command_json,
            request_json,
        )
        now = _utc_now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM local_queue_submissions
                 WHERE submission_id = ? OR add_request_id = ?
                    OR (cid = ? AND sid = ? AND client_message_id = ?)
                    OR (cid = ? AND sid = ? AND turn_id = ?)
                """,
                (
                    normalized_submission_id,
                    normalized_add_request_id,
                    request.cid,
                    request.sid,
                    normalized_client_message_id,
                    request.cid,
                    request.sid,
                    request.turn_id,
                ),
            ).fetchall()
            if rows:
                if len(rows) != 1 or str(rows[0]["fingerprint"]) != fingerprint:
                    raise DurableQueuePersistenceConflict(
                        "durable queue identity conflicts with persisted snapshot"
                    )
                connection.commit()
                return _snapshot_from_row(rows[0])

            connection.execute(
                """
                INSERT INTO local_queue_submissions (
                    submission_id, client_message_id, cid, sid, turn_id,
                    add_request_id, command_json, request_json, fingerprint,
                    status, revision, queue_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'adding', 1, 0, ?, ?)
                """,
                (
                    normalized_submission_id,
                    normalized_client_message_id,
                    request.cid,
                    request.sid,
                    request.turn_id,
                    normalized_add_request_id,
                    command_json,
                    request_json,
                    fingerprint,
                    now,
                    now,
                ),
            )
            row = _require_row(connection, normalized_submission_id)
            connection.commit()
            return _snapshot_from_row(row)
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise DurableQueuePersistenceConflict(
                "durable queue identity conflicts with persisted snapshot"
            ) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _find(self, submission_id: str) -> LocalDurableQueueSnapshot | None:
        """同步读取一个本地 Queue 快照。"""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM local_queue_submissions WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()
            return _snapshot_from_row(row) if row is not None else None
        finally:
            connection.close()

    def _list_session(
        self,
        cid: str,
        sid: str,
    ) -> tuple[LocalDurableQueueSnapshot, ...]:
        """同步读取 Session 的本地 Queue 快照。"""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM local_queue_submissions
                 WHERE cid = ? AND sid = ?
                 ORDER BY created_at, submission_id
                """,
                (cid, sid),
            ).fetchall()
            return tuple(_snapshot_from_row(row) for row in rows)
        finally:
            connection.close()

    def _mark_queued(
        self,
        submission_id: str,
        add_request_id: str,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """同步提交远端 queued 事实。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _require_row(connection, submission_id)
            if str(row["add_request_id"]) != add_request_id:
                raise DurableQueuePersistenceConflict(
                    "durable queue add request identity changed"
                )
            status = str(row["status"])
            if status not in {"adding", "queued"}:
                raise DurableQueuePersistenceConflict(
                    "durable queue item cannot return to queued"
                )
            _require_monotonic_version(row, queue_version)
            if status == "adding" or int(row["queue_version"]) != queue_version:
                _update_state(
                    connection,
                    submission_id,
                    status="queued",
                    queue_version=queue_version,
                )
            result = _require_row(connection, submission_id)
            connection.commit()
            return _snapshot_from_row(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _begin_start(
        self,
        submission_id: str,
        request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """同步登记 start 尝试，并让相同尝试保持幂等。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _require_row(connection, submission_id)
            status = str(row["status"])
            existing_request_id = str(row["start_request_id"] or "")
            if status in {"starting", "started"}:
                if existing_request_id != request_id:
                    raise DurableQueuePersistenceConflict(
                        "durable queue start is already in progress"
                    )
                connection.commit()
                return _snapshot_from_row(row)
            if status != "queued":
                raise DurableQueuePersistenceConflict(
                    "only a queued durable submission can start"
                )
            connection.execute(
                """
                UPDATE local_queue_submissions
                   SET status = 'starting', start_request_id = ?,
                       revision = revision + 1, updated_at = ?
                 WHERE submission_id = ?
                """,
                (request_id, _utc_now(), submission_id),
            )
            result = _require_row(connection, submission_id)
            connection.commit()
            return _snapshot_from_row(result)
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise DurableQueuePersistenceConflict(
                "durable queue start request identity conflicts"
            ) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _mark_started(
        self,
        submission_id: str,
        request_id: str,
        turn_id: str,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """同步提交远端 started 事实。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _require_row(connection, submission_id)
            if (
                str(row["start_request_id"] or "") != request_id
                or str(row["turn_id"]) != turn_id
            ):
                raise DurableQueuePersistenceConflict(
                    "durable queue start receipt identity changed"
                )
            if str(row["status"]) not in {"starting", "started"}:
                raise DurableQueuePersistenceConflict(
                    "durable queue item was not starting"
                )
            _require_monotonic_version(row, queue_version)
            if (
                str(row["status"]) == "starting"
                or int(row["queue_version"]) != queue_version
            ):
                _update_state(
                    connection,
                    submission_id,
                    status="started",
                    queue_version=queue_version,
                )
            result = _require_row(connection, submission_id)
            connection.commit()
            return _snapshot_from_row(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _mark_settled(
        self,
        submission_id: str,
    ) -> LocalDurableQueueSnapshot:
        """同步提交 Queue Turn 已完成观察的恢复状态。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _require_row(connection, submission_id)
            status = str(row["status"])
            if status not in {"started", "settled"}:
                raise DurableQueuePersistenceConflict(
                    "only a started durable queue item can settle"
                )
            if status == "started":
                _update_state(
                    connection,
                    submission_id,
                    status="settled",
                    queue_version=int(row["queue_version"]),
                )
            result = _require_row(connection, submission_id)
            connection.commit()
            return _snapshot_from_row(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _reset_start(
        self,
        submission_id: str,
        request_id: str,
    ) -> LocalDurableQueueSnapshot:
        """同步清除确定未执行的 start 尝试。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _require_row(connection, submission_id)
            if str(row["status"]) != "starting":
                raise DurableQueuePersistenceConflict(
                    "durable queue item is not starting"
                )
            if str(row["start_request_id"] or "") != request_id:
                raise DurableQueuePersistenceConflict(
                    "durable queue start request identity changed"
                )
            connection.execute(
                """
                UPDATE local_queue_submissions
                   SET status = 'queued', start_request_id = NULL,
                       revision = revision + 1, updated_at = ?
                 WHERE submission_id = ?
                """,
                (_utc_now(), submission_id),
            )
            result = _require_row(connection, submission_id)
            connection.commit()
            return _snapshot_from_row(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _mark_deleted(
        self,
        submission_id: str,
        queue_version: int,
    ) -> LocalDurableQueueSnapshot:
        """同步提交远端 deleted 事实。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _require_row(connection, submission_id)
            if str(row["status"]) == "started":
                raise DurableQueuePersistenceConflict(
                    "started durable queue item cannot be deleted"
                )
            _require_monotonic_version(row, queue_version)
            if (
                str(row["status"]) != "deleted"
                or int(row["queue_version"]) != queue_version
            ):
                _update_state(
                    connection,
                    submission_id,
                    status="deleted",
                    queue_version=queue_version,
                )
            result = _require_row(connection, submission_id)
            connection.commit()
            return _snapshot_from_row(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def _update_state(
    connection: sqlite3.Connection,
    submission_id: str,
    *,
    status: str,
    queue_version: int,
) -> None:
    """原子推进一项本地 Queue 投影。"""
    connection.execute(
        """
        UPDATE local_queue_submissions
           SET status = ?, queue_version = ?, revision = revision + 1,
               updated_at = ?
         WHERE submission_id = ?
        """,
        (status, queue_version, _utc_now(), submission_id),
    )


def _snapshot_from_row(row: sqlite3.Row) -> LocalDurableQueueSnapshot:
    """把数据库行还原为经过完整校验的本地 Queue 快照。"""
    command_value = json.loads(str(row["command_json"]))
    request_value = json.loads(str(row["request_json"]))
    if not isinstance(command_value, dict) or not isinstance(request_value, dict):
        raise RuntimeError("durable queue persisted snapshot is malformed")
    return LocalDurableQueueSnapshot(
        submission_id=str(row["submission_id"]),
        client_message_id=str(row["client_message_id"]),
        add_request_id=str(row["add_request_id"]),
        command=SubmitTurnCommand.from_dict(command_value),
        request=ModelStreamRequest.from_dict(request_value),
        status=str(row["status"]),
        revision=int(row["revision"]),
        queue_version=int(row["queue_version"]),
        start_request_id=(
            str(row["start_request_id"])
            if row["start_request_id"] is not None
            else None
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _require_row(
    connection: sqlite3.Connection,
    submission_id: str,
) -> sqlite3.Row:
    """读取必须存在的本地 Queue 快照。"""
    row = connection.execute(
        "SELECT * FROM local_queue_submissions WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    if row is None:
        raise LookupError("durable queue execution snapshot is unavailable")
    return row


def _validate_command_coordinates(
    command: SubmitTurnCommand,
    request: ModelStreamRequest,
) -> None:
    """要求本地 Command 与远端请求绑定同一 Turn。"""
    remote_turn = command.trace_context.get("remote_turn")
    if not isinstance(remote_turn, Mapping):
        raise ValueError("durable queue command requires remote turn coordinates")
    if any(
        remote_turn.get(field_name) != getattr(request, field_name)
        for field_name in ("cid", "sid", "turn_id")
    ):
        raise ValueError("durable queue command coordinates do not match request")


def _require_monotonic_version(row: sqlite3.Row, queue_version: int) -> None:
    """拒绝使用陈旧服务端 Queue 版本倒退本地投影。"""
    if queue_version < int(row["queue_version"]):
        raise DurableQueuePersistenceConflict(
            "durable queue version cannot move backwards"
        )


def _fingerprint(*values: str) -> str:
    """计算本地 Queue 冻结意图的确定指纹。"""
    encoded = "\x1f".join(values).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_json(value: typing.Mapping[str, ThawedJsonValue]) -> str:
    """使用稳定 JSON 编码持久化冻结快照。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _session_coordinates(cid: str, sid: str) -> tuple[str, str]:
    """校验本地 Queue Session 坐标。"""
    normalized_cid = str(cid or "").strip()
    normalized_sid = str(sid or "").strip()
    if not normalized_cid or not normalized_sid:
        raise ValueError("durable queue requires cid and sid")
    return normalized_cid, normalized_sid


def _client_message_id(value: str) -> str:
    """校验 Queue 输入 exactly-once identity。"""
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("client_message_id must contain 1-128 characters")
    return normalized


def _queue_version(value: int) -> None:
    """校验服务端 Queue 版本。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("queue_version must be non-negative")


def _utc_now() -> str:
    """返回可排序的 UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


if __name__ == '__main__':
    pass
