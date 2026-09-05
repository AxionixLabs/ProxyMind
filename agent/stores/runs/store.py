# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import hashlib
import json
import sqlite3
import typing
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

from agent.domain import (
    RECOVERABLE_RUN_STATUSES,
    RunStatus,
    validate_run_transition,
)
from agent.ports import (
    RecoveryResolution,
    RemoteTurnExecutionSnapshot,
    RunFact,
    RunPersistenceConflict,
    RunRecoveryResolutionRecord,
    RunSnapshot,
    remote_turn_binding,
)
from agent.protocol import (
    ModelStreamRequest,
    RunEvent,
    SubmitTurnCommand,
)
from agent.protocol.json_value import (
    ThawedJsonValue,
    thaw_json,
)
from .records import (
    encode_json,
    outbox_update,
    run_effect_id,
    run_status_for_event,
    snapshot_from_row,
    terminal_facts,
)
from .schema import (
    RUN_SNAPSHOT_VERSION,
    RUN_STORE_SCHEMA_SQL,
    RUN_STORE_SCHEMA_VERSION,
)


class SQLiteRunStore:
    """使用独立 SQLite 文件事务保存 Run 事件、快照、事实和 outbox。"""

    def __init__(self, db_path: str | Path) -> None:
        """绑定 Agent Harness 专用数据库文件。"""
        self.db_path = Path(db_path).expanduser()

    async def append_event(
        self,
        command: SubmitTurnCommand,
        event: RunEvent,
    ) -> None:
        """在线程中的单个立即事务内提交 Run 状态事实。"""
        await asyncio.to_thread(self._append_event, command, event)

    async def find_run(
        self,
        command: SubmitTurnCommand,
    ) -> RunSnapshot | None:
        """按 Run、Command 或幂等身份查找并校验同一意图。"""
        return await asyncio.to_thread(self._find_run, command)

    async def recover_session(
        self,
        session_id: str,
    ) -> tuple[RunSnapshot, ...]:
        """按更新时间读取 Session 中尚未终结的 Run。"""
        normalized = str(session_id or "").strip()
        if not normalized:
            raise ValueError("session_id is required")
        return await asyncio.to_thread(self._recover_session, normalized)

    async def resolve_recovery(
        self,
        run_id: str,
        *,
        request_id: str,
        resolution: RecoveryResolution,
        result_payload: typing.Mapping[str, ThawedJsonValue] | None = None,
        error: str = "",
    ) -> RunRecoveryResolutionRecord:
        """幂等保存权威恢复结论，并从 Session 恢复门禁中移除该 Run。"""
        normalized_run_id = str(run_id or "").strip()
        normalized_request_id = str(request_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id is required")
        if not normalized_request_id:
            raise ValueError("request_id is required")
        if resolution not in {"committed", "failed", "not_executed"}:
            raise ValueError("unsupported recovery resolution")
        if resolution == "committed" and result_payload is None:
            raise ValueError("committed recovery requires result_payload")
        if resolution == "failed" and not str(error or "").strip():
            raise ValueError("failed recovery requires error")
        if result_payload is not None and not isinstance(
            result_payload,
            typing.Mapping,
        ):
            raise TypeError("recovery result_payload must be an object")
        normalized_error = str(error or "")[:2000]
        normalized_result = (
            dict(result_payload)
            if result_payload is not None
            else None
        )
        return await asyncio.to_thread(
            self._resolve_recovery,
            normalized_run_id,
            normalized_request_id,
            resolution,
            normalized_result,
            normalized_error,
        )

    async def save_remote_request(
        self,
        command: SubmitTurnCommand,
        request: ModelStreamRequest,
    ) -> RemoteTurnExecutionSnapshot:
        """在线程内保存或推进 Run 最新的冻结远端请求。"""
        if not isinstance(command, SubmitTurnCommand):
            raise TypeError("remote request command is invalid")
        if not isinstance(request, ModelStreamRequest):
            raise TypeError("remote model request is invalid")
        return await asyncio.to_thread(
            self._save_remote_request,
            command,
            request,
        )

    async def load_remote_request(
        self,
        run_id: str,
    ) -> RemoteTurnExecutionSnapshot | None:
        """在线程内读取 Run 最新的冻结远端请求。"""
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id is required")
        return await asyncio.to_thread(self._load_remote_request, normalized)

    async def load_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[RunEvent, ...]:
        """读取事件游标之后的不可变 Run 事件。"""
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id is required")
        if isinstance(after_sequence, bool) or after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        return await asyncio.to_thread(
            self._load_events,
            normalized,
            after_sequence,
        )

    async def load_facts(
        self,
        run_id: str,
        *,
        kind: str | None = None,
    ) -> tuple[RunFact, ...]:
        """读取指定 Run 的最终可恢复业务事实。"""
        normalized = str(run_id or "").strip()
        if not normalized:
            raise ValueError("run_id is required")
        normalized_kind = str(kind or "").strip() or None
        return await asyncio.to_thread(
            self._load_facts,
            normalized,
            normalized_kind,
        )

    def _connect(self) -> sqlite3.Connection:
        """建立启用外键、WAL 和 FULL 同步的连接并校验 schema 版本。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        current_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if current_version > RUN_STORE_SCHEMA_VERSION:
            connection.close()
            raise RuntimeError("agent runtime store schema is newer than this client")
        connection.executescript(RUN_STORE_SCHEMA_SQL)
        if current_version < RUN_STORE_SCHEMA_VERSION:
            connection.execute(
                f"PRAGMA user_version={RUN_STORE_SCHEMA_VERSION}"
            )
        return connection

    def _append_event(
        self,
        command: SubmitTurnCommand,
        event: RunEvent,
    ) -> None:
        """校验单写者序列并执行原子状态提交。"""
        if event.session_id != command.session_id or event.run_id != command.run_id:
            raise RunPersistenceConflict("event coordinates do not match command")
        target_status = run_status_for_event(event.kind)
        payload_status = str(event.payload.get("status") or "").strip()
        if target_status is None or payload_status != target_status.value:
            raise RunPersistenceConflict("event kind does not match run status")

        encoded_event = encode_json(event.to_dict())
        encoded_command = encode_json(command.to_dict())
        fingerprint = command.fingerprint()
        effect_id = run_effect_id(command, fingerprint)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM run_snapshots WHERE run_id = ?",
                (command.run_id,),
            ).fetchone()

            if row is None:
                self._insert_initial_run(
                    connection,
                    command=command,
                    event=event,
                    encoded_command=encoded_command,
                    fingerprint=fingerprint,
                    effect_id=effect_id,
                    target_status=target_status,
                )
            else:
                self._advance_run(
                    connection,
                    row=row,
                    command=command,
                    event=event,
                    fingerprint=fingerprint,
                    target_status=target_status,
                    encoded_event=encoded_event,
                )
                connection.commit()
                return None

            connection.execute(
                """
                INSERT INTO run_events (
                    run_id, sequence, event_id, session_id, kind,
                    event_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    event.sequence,
                    event.event_id,
                    event.session_id,
                    event.kind,
                    encoded_event,
                    event.occurred_at,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise RunPersistenceConflict(
                "run identity or event conflicts with persisted state"
            ) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _save_remote_request(
        self,
        command: SubmitTurnCommand,
        request: ModelStreamRequest,
    ) -> RemoteTurnExecutionSnapshot:
        """在一个立即事务中保存首个请求或推进 continuation 请求。"""
        binding = remote_turn_binding(command)
        if binding is None:
            raise RunPersistenceConflict(
                "remote request command has no turn binding"
            )
        if request.cid != binding.cid or request.sid != binding.sid:
            raise RunPersistenceConflict(
                "remote request session does not match command binding"
            )

        encoded_request = encode_json(request.to_dict())
        fingerprint = hashlib.sha256(
            encoded_request.encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            run_row = connection.execute(
                "SELECT command_json FROM run_snapshots WHERE run_id = ?",
                (command.run_id,),
            ).fetchone()
            if run_row is None:
                raise RunPersistenceConflict(
                    "remote request run has not been persisted"
                )
            if str(run_row["command_json"]) != encode_json(command.to_dict()):
                raise RunPersistenceConflict(
                    "remote request command conflicts with persisted run"
                )

            row = connection.execute(
                "SELECT * FROM run_remote_requests WHERE run_id = ?",
                (command.run_id,),
            ).fetchone()
            if row is None:
                if request.turn_id != binding.turn_id:
                    raise RunPersistenceConflict(
                        "initial remote request turn does not match command binding"
                    )
                connection.execute(
                    """
                    INSERT INTO run_remote_requests (
                        run_id, cid, sid, turn_id, request_json, fingerprint,
                        revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        command.run_id,
                        request.cid,
                        request.sid,
                        request.turn_id,
                        encoded_request,
                        fingerprint,
                        now,
                        now,
                    ),
                )
            elif str(row["turn_id"]) == request.turn_id:
                if str(row["fingerprint"]) != fingerprint:
                    raise RunPersistenceConflict(
                        "remote request turn conflicts with persisted snapshot"
                    )
            else:
                connection.execute(
                    """
                    UPDATE run_remote_requests
                       SET turn_id = ?, request_json = ?, fingerprint = ?,
                           revision = revision + 1, updated_at = ?
                     WHERE run_id = ?
                    """,
                    (
                        request.turn_id,
                        encoded_request,
                        fingerprint,
                        now,
                        command.run_id,
                    ),
                )
            persisted = connection.execute(
                "SELECT * FROM run_remote_requests WHERE run_id = ?",
                (command.run_id,),
            ).fetchone()
            connection.commit()
            if persisted is None:
                raise RuntimeError("remote request snapshot was not persisted")
            return _remote_request_snapshot(persisted)
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise RunPersistenceConflict(
                "remote request identity conflicts with persisted snapshot"
            ) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _load_remote_request(
        self,
        run_id: str,
    ) -> RemoteTurnExecutionSnapshot | None:
        """同步读取并重新校验 Run 的最新冻结请求。"""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM run_remote_requests WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return _remote_request_snapshot(row) if row is not None else None
        finally:
            connection.close()

    def _insert_initial_run(
        self,
        connection: sqlite3.Connection,
        *,
        command: SubmitTurnCommand,
        event: RunEvent,
        encoded_command: str,
        fingerprint: str,
        effect_id: str,
        target_status: RunStatus,
    ) -> None:
        """创建只允许从 queued 开始的快照和待派发 outbox。"""
        if target_status is not RunStatus.QUEUED or event.sequence != 1:
            raise RunPersistenceConflict("new run must start with queued sequence 1")
        connection.execute(
            """
            INSERT INTO run_snapshots (
                run_id, session_id, command_id, idempotency_key,
                fingerprint, command_json, status, sequence,
                snapshot_version, effect_id, effect_status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                command.run_id,
                command.session_id,
                command.command_id,
                command.idempotency_key,
                fingerprint,
                encoded_command,
                target_status.value,
                event.sequence,
                RUN_SNAPSHOT_VERSION,
                effect_id,
                event.occurred_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO run_outbox (
                effect_id, run_id, session_id, fingerprint, replay,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'manual', 'pending', ?, ?)
            """,
            (
                effect_id,
                command.run_id,
                command.session_id,
                fingerprint,
                event.occurred_at,
                event.occurred_at,
            ),
        )

    def _advance_run(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        command: SubmitTurnCommand,
        event: RunEvent,
        fingerprint: str,
        target_status: RunStatus,
        encoded_event: str,
    ) -> None:
        """推进现有快照，并让完全相同的事务重试保持幂等。"""
        self._validate_identity(row, command, fingerprint)
        current_sequence = int(row["sequence"])
        if event.sequence <= current_sequence:
            persisted = connection.execute(
                "SELECT event_json FROM run_events WHERE run_id = ? AND sequence = ?",
                (event.run_id, event.sequence),
            ).fetchone()
            if (
                event.sequence == current_sequence
                and persisted is not None
                and str(persisted["event_json"]) == encoded_event
            ):
                connection.commit()
                return None
            raise RunPersistenceConflict("run event sequence was already committed")
        if event.sequence != current_sequence + 1:
            raise RunPersistenceConflict("run event sequence is not continuous")

        current_status = RunStatus(str(row["status"]))
        validate_run_transition(current_status, target_status)
        connection.execute(
            """
            INSERT INTO run_events (
                run_id, sequence, event_id, session_id, kind,
                event_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.run_id,
                event.sequence,
                event.event_id,
                event.session_id,
                event.kind,
                encoded_event,
                event.occurred_at,
            ),
        )
        effect_status, result_json, error = outbox_update(event, target_status)
        connection.execute(
            """
            UPDATE run_snapshots
               SET status = ?, sequence = ?, effect_status = ?, updated_at = ?
             WHERE run_id = ?
            """,
            (
                target_status.value,
                event.sequence,
                effect_status,
                event.occurred_at,
                event.run_id,
            ),
        )
        connection.execute(
            """
            UPDATE run_outbox
               SET status = ?,
                   attempt_count = attempt_count + ?,
                   result_json = coalesce(?, result_json),
                   error = ?,
                   updated_at = ?
             WHERE run_id = ?
            """,
            (
                effect_status,
                1 if target_status is RunStatus.RUNNING else 0,
                result_json,
                error,
                event.occurred_at,
                event.run_id,
            ),
        )
        self._insert_terminal_facts(connection, event, target_status)

    @staticmethod
    def _validate_identity(
        row: sqlite3.Row,
        command: SubmitTurnCommand,
        fingerprint: str,
    ) -> None:
        """拒绝 Run、Command 或幂等键被复用于另一项意图。"""
        if (
            str(row["session_id"]) != command.session_id
            or str(row["run_id"]) != command.run_id
            or str(row["idempotency_key"]) != command.idempotency_key
            or str(row["fingerprint"]) != fingerprint
        ):
            raise RunPersistenceConflict("run identity conflicts with persisted command")

    @staticmethod
    def _insert_terminal_facts(
        connection: sqlite3.Connection,
        event: RunEvent,
        status: RunStatus,
    ) -> None:
        """从终态结果提取无需流式 token 即可回读的稳定事实。"""
        if status in RECOVERABLE_RUN_STATUSES:
            return None
        result = event.payload.get("result")
        if not isinstance(result, typing.Mapping):
            return None
        result_value = thaw_json(result)
        if not isinstance(result_value, dict):
            raise RunPersistenceConflict("terminal result must be an object")
        facts = terminal_facts(result_value)
        for kind, position, payload in facts:
            connection.execute(
                """
                INSERT INTO run_facts (
                    run_id, kind, position, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    kind,
                    position,
                    encode_json(payload),
                    event.occurred_at,
                ),
            )

    def _find_run(self, command: SubmitTurnCommand) -> RunSnapshot | None:
        """同步查找一项已有 Run，并拒绝跨身份碰撞。"""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM run_snapshots
                 WHERE run_id = ? OR command_id = ?
                    OR (session_id = ? AND idempotency_key = ?)
                 ORDER BY updated_at, run_id
                """,
                (
                    command.run_id,
                    command.command_id,
                    command.session_id,
                    command.idempotency_key,
                ),
            ).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise RunPersistenceConflict("command identity resolves to multiple runs")
            row = rows[0]
            self._validate_identity(row, command, command.fingerprint())
            return snapshot_from_row(row)
        finally:
            connection.close()

    def _recover_session(self, session_id: str) -> tuple[RunSnapshot, ...]:
        """同步读取 Session 中全部恢复快照。"""
        connection = self._connect()
        try:
            placeholders = ",".join("?" for _ in RECOVERABLE_RUN_STATUSES)
            values = tuple(status.value for status in RECOVERABLE_RUN_STATUSES)
            rows = connection.execute(
                f"""
                SELECT * FROM run_snapshots
                 WHERE session_id = ? AND status IN ({placeholders})
                   AND NOT EXISTS (
                       SELECT 1 FROM run_facts AS recovery
                        WHERE recovery.run_id = run_snapshots.run_id
                          AND recovery.kind = 'recovery_resolution'
                          AND recovery.position = 0
                   )
                 ORDER BY updated_at, run_id
                """,
                (session_id, *values),
            ).fetchall()
            return tuple(snapshot_from_row(row) for row in rows)
        finally:
            connection.close()

    def _resolve_recovery(
        self,
        run_id: str,
        request_id: str,
        resolution: RecoveryResolution,
        result_payload: dict[str, ThawedJsonValue] | None,
        error: str,
    ) -> RunRecoveryResolutionRecord:
        """在单个事务中校验恢复状态并提交幂等决议事实。"""
        resolved_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            snapshot = connection.execute(
                "SELECT status FROM run_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if snapshot is None:
                raise RunPersistenceConflict("run does not exist")
            status = RunStatus(str(snapshot["status"]))
            if status not in RECOVERABLE_RUN_STATUSES:
                raise RunPersistenceConflict(
                    "only recoverable runs can be resolved"
                )

            existing = connection.execute(
                """
                SELECT payload_json FROM run_facts
                 WHERE run_id = ? AND kind = 'recovery_resolution'
                   AND position = 0
                """,
                (run_id,),
            ).fetchone()
            if existing is not None:
                record = self._existing_recovery_resolution(
                    run_id,
                    request_id,
                    resolution,
                    result_payload,
                    error,
                    str(existing["payload_json"]),
                )
                connection.commit()
                return record

            payload = {
                "request_id": request_id,
                "resolution": resolution,
                "result_payload": result_payload,
                "error": error,
                "resolved_at": resolved_at,
            }
            connection.execute(
                """
                INSERT INTO run_facts (
                    run_id, kind, position, payload_json, created_at
                ) VALUES (?, 'recovery_resolution', 0, ?, ?)
                """,
                (run_id, encode_json(payload), resolved_at),
            )
            outbox_status = (
                "committed"
                if resolution == "committed"
                else "failed"
            )
            outbox_result = (
                encode_json(result_payload)
                if result_payload is not None
                else None
            )
            connection.execute(
                """
                UPDATE run_snapshots
                   SET effect_status = ?, updated_at = ?
                 WHERE run_id = ?
                """,
                (outbox_status, resolved_at, run_id),
            )
            connection.execute(
                """
                UPDATE run_outbox
                   SET status = ?,
                       result_json = coalesce(?, result_json),
                       error = ?,
                       updated_at = ?
                 WHERE run_id = ?
                """,
                (
                    outbox_status,
                    outbox_result,
                    error,
                    resolved_at,
                    run_id,
                ),
            )
            connection.commit()
            return RunRecoveryResolutionRecord(
                run_id=run_id,
                request_id=request_id,
                resolution=resolution,
                result_payload=result_payload,
                error=error,
                resolved_at=resolved_at,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _existing_recovery_resolution(
        run_id: str,
        request_id: str,
        resolution: RecoveryResolution,
        result_payload: dict[str, ThawedJsonValue] | None,
        error: str,
        payload_json: str,
    ) -> RunRecoveryResolutionRecord:
        """校验重复核对请求并返回首次提交的不可变结论。"""
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError) as parse_error:
            raise RunPersistenceConflict(
                "persisted recovery resolution is invalid"
            ) from parse_error
        if not isinstance(payload, dict):
            raise RunPersistenceConflict(
                "persisted recovery resolution is invalid"
            )
        if (
            payload.get("request_id") != request_id
            or payload.get("resolution") != resolution
            or payload.get("result_payload") != result_payload
            or payload.get("error") != error
        ):
            raise RunPersistenceConflict(
                "recovery resolution conflicts with persisted decision"
            )
        persisted_result = payload.get("result_payload")
        if persisted_result is not None and not isinstance(
            persisted_result,
            dict,
        ):
            raise RunPersistenceConflict(
                "persisted recovery result is invalid"
            )
        persisted_resolution = payload.get("resolution")
        if persisted_resolution not in {
            "committed",
            "failed",
            "not_executed",
        }:
            raise RunPersistenceConflict(
                "persisted recovery resolution is invalid"
            )
        return RunRecoveryResolutionRecord(
            run_id=run_id,
            request_id=request_id,
            resolution=persisted_resolution,
            result_payload=persisted_result,
            error=str(payload.get("error") or ""),
            resolved_at=str(payload.get("resolved_at") or ""),
        )

    def _load_events(
        self,
        run_id: str,
        after_sequence: int,
    ) -> tuple[RunEvent, ...]:
        """同步读取并重新校验序列化事件。"""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT event_json FROM run_events
                 WHERE run_id = ? AND sequence > ?
                 ORDER BY sequence
                """,
                (run_id, after_sequence),
            ).fetchall()
            return tuple(
                RunEvent.from_dict(json.loads(str(row["event_json"])))
                for row in rows
            )
        finally:
            connection.close()

    def _load_facts(
        self,
        run_id: str,
        kind: str | None,
    ) -> tuple[RunFact, ...]:
        """同步读取终态业务事实。"""
        connection = self._connect()
        try:
            if kind is None:
                rows = connection.execute(
                    """
                    SELECT kind, position, payload_json FROM run_facts
                     WHERE run_id = ? ORDER BY kind, position
                    """,
                    (run_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT kind, position, payload_json FROM run_facts
                     WHERE run_id = ? AND kind = ? ORDER BY position
                    """,
                    (run_id, kind),
                ).fetchall()
            return tuple(
                RunFact(
                    run_id=run_id,
                    kind=str(row["kind"]),
                    position=int(row["position"]),
                    payload=json.loads(str(row["payload_json"])),
                )
                for row in rows
            )
        finally:
            connection.close()


def _remote_request_snapshot(
    row: sqlite3.Row,
) -> RemoteTurnExecutionSnapshot:
    """把数据库行还原为经过完整校验的冻结远端请求。"""
    request_value = json.loads(str(row["request_json"]))
    if not isinstance(request_value, dict):
        raise RunPersistenceConflict(
            "persisted remote request snapshot is malformed"
        )
    request = ModelStreamRequest.from_dict(request_value)
    if (
        request.cid != str(row["cid"])
        or request.sid != str(row["sid"])
        or request.turn_id != str(row["turn_id"])
    ):
        raise RunPersistenceConflict(
            "persisted remote request coordinates are inconsistent"
        )
    return RemoteTurnExecutionSnapshot(
        run_id=str(row["run_id"]),
        request=request,
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


if __name__ == '__main__':
    pass
