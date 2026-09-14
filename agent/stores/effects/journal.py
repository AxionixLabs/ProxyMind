# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import json
import sqlite3
import time
import typing
from pathlib import Path

from agent.ports import (
    EffectIntent,
    EffectJournalDecision,
    EffectJournalPersistenceError,
)
from agent.protocol.json_value import (
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS local_effects (
    effect_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    replay TEXT NOT NULL,
    status TEXT NOT NULL,
    result_payload TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS local_tool_results (
    cid TEXT NOT NULL,
    sid TEXT NOT NULL,
    call_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    original_result TEXT NOT NULL,
    PRIMARY KEY (cid, sid, call_id)
);
"""

EFFECT_STORE_SCHEMA_VERSION: typing.Final = 2


class LocalEffectJournal:
    """持久记录本地效果证据和工具结果，两个身份空间分别原子去重。"""

    def __init__(self, db_path: str | Path) -> None:
        """绑定本地效果和工具结果共用的执行账本文件。"""
        self.db_path = Path(db_path).expanduser()

    async def save_tool_result(
        self,
        cid: str,
        sid: str,
        call_id: str,
        payload: dict[str, ThawedJsonValue],
        original_result: dict[str, ThawedJsonValue],
    ) -> None:
        """在首次投递前保存冻结结果，跨进程重试保持相同载荷。"""
        encoded = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        original = json.dumps(
            original_result, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        await asyncio.to_thread(self._save_tool_result, cid, sid, call_id, encoded, original)

    def _save_tool_result(
        self, cid: str, sid: str, call_id: str, encoded: str, original: str,
    ) -> None:
        """以唯一调用键原子保存结果并拒绝内容冲突。"""
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO local_tool_results "
                    "(cid, sid, call_id, payload, original_result) VALUES (?, ?, ?, ?, ?)",
                    (cid, sid, call_id, encoded, original),
                )
                row = connection.execute(
                    "SELECT payload FROM local_tool_results WHERE cid = ? AND sid = ? AND call_id = ?",
                    (cid, sid, call_id),
                ).fetchone()
                if row is None or row["payload"] != encoded:
                    raise ValueError("local tool result conflicts with persisted execution evidence")
        finally:
            connection.close()

    async def load_tool_result(
        self, cid: str, sid: str, call_id: str,
    ) -> dict[str, ThawedJsonValue] | None:
        """读取历史调用的确定交付结果。"""
        return await asyncio.to_thread(self._load_tool_result, cid, sid, call_id)

    def _load_tool_result(self, cid: str, sid: str, call_id: str) -> dict[str, ThawedJsonValue] | None:
        """在短连接中读取结果，解码后的完整契约由调用边界校验。"""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT payload FROM local_tool_results WHERE cid = ? AND sid = ? AND call_id = ?",
                (cid, sid, call_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            raise ValueError("persisted tool result must be an object")
        return thaw_object(
            freeze_json(payload, field_name="persisted tool result"),
            field_name="persisted tool result",
        )

    async def inspect(self, effect: EffectIntent) -> EffectJournalDecision:
        """不取得执行权地读取已提交结果或核对要求。"""
        return await asyncio.to_thread(self._inspect, effect)

    async def begin(self, effect: EffectIntent) -> EffectJournalDecision:
        """原子取得执行权，或返回已提交结果与核对要求。"""
        return await asyncio.to_thread(self._begin, effect)

    async def commit(
        self,
        effect: EffectIntent,
        result_payload: dict[str, typing.Any]
    ) -> None:
        """持久提交一次已知的本地效果结果。"""
        await asyncio.to_thread(self._commit, effect, result_payload)

    async def mark_unknown(
        self,
        effect: EffectIntent,
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
        """建立启用 WAL 的连接并校验效果账本 schema 版本。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        current_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if current_version > EFFECT_STORE_SCHEMA_VERSION:
            connection.close()
            raise RuntimeError("effect store schema is newer than this client")
        connection.executescript(_SCHEMA_SQL)
        if current_version < EFFECT_STORE_SCHEMA_VERSION:
            connection.execute(
                f"PRAGMA user_version={EFFECT_STORE_SCHEMA_VERSION}"
            )
        return connection

    def _begin(self, effect: EffectIntent) -> EffectJournalDecision:
        """在同步事务中决定效果的下一步动作。"""
        _validate_effect(effect)
        now_ms = int(time.time() * 1000)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO local_effects (
                    effect_id, fingerprint, replay, status,
                    created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, 'prepared', ?, ?)
                """,
                (
                    effect.effect_id,
                    effect.fingerprint,
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
            if str(row["replay"]) != effect.replay:
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
                   SET status = 'dispatching', error = '', updated_at_ms = ?
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

    def _inspect(self, effect: EffectIntent) -> EffectJournalDecision:
        """在同步连接中读取效果状态，但不创建或更新效果记录。"""
        _validate_effect(effect)
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
            if str(row["replay"]) != effect.replay:
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
        effect: EffectIntent,
        result_payload: dict[str, typing.Any]
    ) -> None:
        """在同步事务中提交规范化结果。"""
        _validate_effect(effect)
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
        effect: EffectIntent,
        error: str,
        result_payload: dict[str, typing.Any] | None,
    ) -> None:
        """在同步事务中持久化不确定状态及候选结果。"""
        _validate_effect(effect)
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
                cursor = connection.execute(
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
                if cursor.rowcount != 1:
                    raise ValueError("local effect identity is invalid")
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
            if "execution" in result:
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


def _validate_effect(effect: EffectIntent) -> None:
    """校验效果身份、SHA-256 指纹和显式重放策略。"""
    effect_id = str(effect.effect_id or "").strip()
    fingerprint = str(effect.fingerprint or "").strip().lower()
    if not effect_id:
        raise ValueError("effect_id is required")
    if (
        len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
    ):
        raise ValueError("effect fingerprint must be SHA-256")
    if effect.replay not in {"safe", "manual"}:
        raise ValueError("effect replay policy is invalid")


if __name__ == '__main__':
    pass
