# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from mind_app.paths import effect_journal_db_path
from mind_nova.stream_events import ExecutionEffect

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS local_effects (
    effect_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    effect_class TEXT NOT NULL,
    replay_policy TEXT NOT NULL,
    status TEXT NOT NULL,
    dispatch_count INTEGER NOT NULL DEFAULT 0,
    result_payload TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
"""


class LocalEffectReconciliationRequired(RuntimeError):
    """表示本地效果结果不确定，必须先核对再继续。"""

    def __init__(self, effect_id: str) -> None:
        """保存需要核对的效果标识。"""
        self.effect_id = str(effect_id or "").strip()
        super().__init__(f"local effect requires reconciliation: {self.effect_id}")


class EffectJournalPersistenceError(RuntimeError):
    """表示本地效果账本无法完成持久化操作。"""


@dataclass(frozen=True, slots=True)
class EffectJournalDecision:
    """描述本地效果是否执行、复用或等待核对。"""
    action: typing.Literal["execute", "reuse", "reconcile"]
    result_payload: dict[str, typing.Any] | None = None


class LocalEffectJournal:
    """按 effect_id 与 fingerprint 持久记录本地副作用结果。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        """绑定独立的本地效果账本文件。"""
        self.db_path = Path(db_path or effect_journal_db_path()).expanduser()

    async def inspect(self, effect: ExecutionEffect) -> EffectJournalDecision:
        """不取得执行权地读取已提交结果或核对要求。"""
        return await asyncio.to_thread(self._inspect, effect)

    async def begin(self, effect: ExecutionEffect) -> EffectJournalDecision:
        """原子取得执行权，或返回已提交结果与核对要求。"""
        return await asyncio.to_thread(self._begin, effect)

    async def commit(
        self,
        effect: ExecutionEffect,
        result_payload: dict[str, typing.Any]
    ) -> None:
        """持久提交一次已知的本地效果结果。"""
        await asyncio.to_thread(self._commit, effect, result_payload)

    async def mark_unknown(
        self,
        effect: ExecutionEffect,
        error: BaseException,
        *,
        result_payload: dict[str, typing.Any] | None = None
    ) -> None:
        """把不确定效果及已知候选结果持久化为待核对状态。"""
        await asyncio.to_thread(
            self._mark_unknown,
            effect,
            f"{type(error).__name__}: {error}"[:2000],
            result_payload,
        )

    async def reconciliation_result(
        self,
        effect_id: str
    ) -> dict[str, typing.Any] | None:
        """读取可证明本地效果已经完成的服务端核对结果。"""
        return await asyncio.to_thread(self._reconciliation_result, effect_id)

    async def mark_reconciled(self, effect_id: str) -> None:
        """把已由服务端确认的候选结果收敛为本地已提交状态。"""
        try:
            await asyncio.to_thread(self._mark_reconciled, effect_id)
        except (OSError, sqlite3.Error) as error:
            raise EffectJournalPersistenceError(
                "failed to persist reconciled local effect"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        """建立启用 WAL 和立即事务的 SQLite 连接。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(_SCHEMA_SQL)
        return connection

    def _begin(self, effect: ExecutionEffect) -> EffectJournalDecision:
        """在同步事务中决定效果的下一步动作。"""
        now_ms = int(time.time() * 1000)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO local_effects (
                    effect_id, fingerprint, effect_class, replay_policy,
                    status, dispatch_count, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, 'prepared', 0, ?, ?)
                """,
                (
                    effect.effect_id,
                    effect.fingerprint,
                    "read_only" if effect.replay == "safe" else "non_replayable",
                    effect.replay,
                    now_ms,
                    now_ms,
                ),
            )
            row = connection.execute(
                "SELECT * FROM local_effects WHERE effect_id = ?",
                (effect.effect_id,),
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("local effect record was not persisted")
            if str(row["fingerprint"]) != effect.fingerprint:
                raise ValueError("local effect fingerprint conflicts with persisted semantics")
            if str(row["replay_policy"]) != effect.replay:
                raise ValueError("local effect replay policy conflicts with persisted semantics")

            status = str(row["status"])
            if status == "committed":
                payload = json.loads(str(row["result_payload"] or "{}"))
                if not isinstance(payload, dict):
                    raise ValueError("local effect result payload is invalid")
                connection.commit()
                return EffectJournalDecision("reuse", payload)

            uncertain = status in {
                "dispatching",
                "unknown",
                "reconciliation_required",
            }
            if uncertain and effect.replay == "manual":
                connection.execute(
                    """
                    UPDATE local_effects
                       SET status = 'reconciliation_required', updated_at_ms = ?
                     WHERE effect_id = ?
                    """,
                    (now_ms, effect.effect_id),
                )
                connection.commit()
                return EffectJournalDecision("reconcile")

            connection.execute(
                """
                UPDATE local_effects
                   SET status = 'dispatching', dispatch_count = dispatch_count + 1,
                       error = '', updated_at_ms = ?
                 WHERE effect_id = ?
                """,
                (now_ms, effect.effect_id),
            )
            connection.commit()
            return EffectJournalDecision("execute")
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _inspect(self, effect: ExecutionEffect) -> EffectJournalDecision:
        """在同步连接中读取效果状态，但不创建或更新效果记录。"""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM local_effects WHERE effect_id = ?",
                (effect.effect_id,),
            ).fetchone()
            if row is None:
                return EffectJournalDecision("execute")
            if str(row["fingerprint"]) != effect.fingerprint:
                raise ValueError("local effect fingerprint conflicts with persisted semantics")
            if str(row["replay_policy"]) != effect.replay:
                raise ValueError("local effect replay policy conflicts with persisted semantics")

            status = str(row["status"])
            if status == "committed":
                payload = json.loads(str(row["result_payload"] or "{}"))
                if not isinstance(payload, dict):
                    raise ValueError("local effect result payload is invalid")
                return EffectJournalDecision("reuse", payload)
            if status in {
                "dispatching",
                "unknown",
                "reconciliation_required",
            } and effect.replay == "manual":
                return EffectJournalDecision("reconcile")
            return EffectJournalDecision("execute")
        finally:
            connection.close()

    def _commit(
        self,
        effect: ExecutionEffect,
        result_payload: dict[str, typing.Any]
    ) -> None:
        """在同步事务中提交规范化结果。"""
        encoded = json.dumps(
            result_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE local_effects
                       SET status = 'committed', result_payload = ?, error = '',
                           updated_at_ms = ?
                     WHERE effect_id = ? AND fingerprint = ?
                    """,
                    (
                        encoded,
                        int(time.time() * 1000),
                        effect.effect_id,
                        effect.fingerprint,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("local effect commit identity is invalid")
        finally:
            connection.close()

    def _mark_unknown(
        self,
        effect: ExecutionEffect,
        error: str,
        result_payload: dict[str, typing.Any] | None,
    ) -> None:
        """在同步事务中持久化不确定状态及候选结果。"""
        encoded_result = (
            json.dumps(
                result_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if result_payload is not None
            else None
        )
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE local_effects
                       SET status = 'reconciliation_required', error = ?,
                           result_payload = coalesce(?, result_payload),
                           updated_at_ms = ?
                     WHERE effect_id = ? AND fingerprint = ?
                    """,
                    (
                        error,
                        encoded_result,
                        int(time.time() * 1000),
                        effect.effect_id,
                        effect.fingerprint,
                    ),
                )
        finally:
            connection.close()

    def _reconciliation_result(
        self,
        effect_id: str,
    ) -> dict[str, typing.Any] | None:
        """在同步连接中读取已提交或有确定候选的核对结果。"""
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT status, result_payload
                  FROM local_effects
                 WHERE effect_id = ?
                """,
                (str(effect_id or "").strip(),),
            ).fetchone()
            if row is None or str(row["status"]) not in {
                "committed",
                "reconciliation_required",
            }:
                return None
            payload = json.loads(str(row["result_payload"] or "{}"))
            if not isinstance(payload, dict):
                return None
            result = payload.get("reconciliation_result_payload")
            if not isinstance(result, dict):
                return None
            execution = result.get("execution")
            persisted_effect = (
                execution.get("effect")
                if isinstance(execution, dict)
                else None
            )
            persisted_effect_id = (
                persisted_effect.get("effect_id")
                if isinstance(persisted_effect, dict)
                else None
            )
            if persisted_effect_id != str(effect_id or "").strip():
                return None
            return result
        finally:
            connection.close()

    def _mark_reconciled(self, effect_id: str) -> None:
        """在同步事务中确认候选结果已经由服务端接受。"""
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE local_effects
                       SET status = 'committed', error = '', updated_at_ms = ?
                     WHERE effect_id = ?
                       AND status IN ('committed', 'reconciliation_required')
                       AND result_payload IS NOT NULL
                    """,
                    (
                        int(time.time() * 1000),
                        str(effect_id or "").strip(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("local effect reconciliation identity is invalid")
        finally:
            connection.close()


if __name__ == "__main__":
    pass
