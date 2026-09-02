# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import json
import sqlite3
from pathlib import Path

from agent.domain.approvals import (
    ActionFingerprint,
    AmendmentOperation,
    ApprovalAction,
    ApprovalActionKind,
    ApprovalAmendment,
    ApprovalDecision,
    ApprovalDecisionKind,
    ApprovalDecisionSource,
    ApprovalFact,
    ApprovalFactState,
    ApprovalIdentity,
    ApprovalOutcome,
    ApprovalResolutionReason,
)

_SCHEMA_VERSION = 1
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS approval_facts (
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL,
    version INTEGER NOT NULL,
    outcome_kind TEXT,
    outcome_fingerprint TEXT,
    outcome_amendment_operation TEXT,
    outcome_amendment_fingerprint TEXT,
    outcome_source TEXT,
    outcome_reason TEXT,
    resolved_at REAL,
    PRIMARY KEY (session_id, run_id, approval_id, action_id)
);
"""


class SQLiteApprovalFactStore:
    """使用 SQLite 保存审批事实，并以事务保证首个终态获胜。"""

    def __init__(self, db_path: str | Path) -> None:
        """绑定审批事实数据库路径。"""
        self.db_path = Path(db_path).expanduser()

    async def record_requested(self, action: ApprovalAction) -> ApprovalFact:
        """记录或幂等返回一次 requested 事实。"""
        return await asyncio.to_thread(self._record_requested, action)

    async def resolve(
        self,
        identity: ApprovalIdentity,
        decision: ApprovalDecision,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> ApprovalFact:
        """在 SQLite 事务中提交审批首个终态。"""
        return await asyncio.to_thread(
            self._resolve,
            identity,
            decision,
            source,
            reason,
            resolved_at,
        )

    async def abandon(
        self,
        identity: ApprovalIdentity,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> ApprovalFact:
        """在 SQLite 事务中提交 abandoned 终态。"""
        return await asyncio.to_thread(
            self._abandon,
            identity,
            source,
            reason,
            resolved_at,
        )

    async def find(self, identity: ApprovalIdentity) -> ApprovalFact | None:
        """按完整审批身份读取事实。"""
        return await asyncio.to_thread(self._find, identity)

    def _connect(self) -> sqlite3.Connection:
        """建立审批事实连接并初始化 schema。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        current_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        if current_version > _SCHEMA_VERSION:
            connection.close()
            raise RuntimeError("approval fact schema is newer than this client")
        connection.executescript(_SCHEMA_SQL)
        if current_version < _SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        return connection

    def _record_requested(self, action: ApprovalAction) -> ApprovalFact:
        """同步创建审批事实并拒绝身份复用。"""
        fact = ApprovalFact.requested(action)
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO approval_facts (
                        session_id, run_id, approval_id, action_id,
                        action_kind, action_fingerprint, state, version
                    ) VALUES (?, ?, ?, ?, ?, ?, 'requested', 0)
                    """,
                    self._identity_values(fact.identity)
                    + (fact.action_kind.value, fact.action_fingerprint.value),
                )
                row = self._select_row(connection, fact.identity)
                if row is None:
                    raise sqlite3.DatabaseError("approval fact was not persisted")
                persisted = _fact_from_row(row)
                if (
                    persisted.action_kind is not fact.action_kind
                    or persisted.action_fingerprint != fact.action_fingerprint
                ):
                    raise ValueError("approval identity conflicts with persisted action")
                return persisted
        finally:
            connection.close()

    def _resolve(
        self,
        identity: ApprovalIdentity,
        decision: ApprovalDecision,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> ApprovalFact:
        """同步以 requested 版本作为 CAS 条件推进事实。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select_row(connection, identity)
            if row is None:
                raise ValueError("approval fact does not exist")
            current = _fact_from_row(row)
            if current.state is not ApprovalFactState.REQUESTED:
                return _same_or_reject_terminal(current, decision)
            resolved = current.resolve(
                decision,
                source=source,
                reason=reason,
                resolved_at=resolved_at,
            )
            updated = connection.execute(
                """
                UPDATE approval_facts
                   SET state = ?, version = ?, outcome_kind = ?,
                       outcome_fingerprint = ?, outcome_amendment_operation = ?,
                       outcome_amendment_fingerprint = ?, outcome_source = ?,
                       outcome_reason = ?, resolved_at = ?
                 WHERE session_id = ? AND run_id = ? AND approval_id = ?
                   AND action_id = ? AND state = 'requested' AND version = 0
                """,
                _outcome_values(resolved)
                + self._identity_values(identity),
            )
            if updated.rowcount != 1:
                raise ValueError("approval fact changed before resolve")
            connection.commit()
            return resolved
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _abandon(
        self,
        identity: ApprovalIdentity,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> ApprovalFact:
        """同步以 requested 版本作为 CAS 条件提交 abandoned。"""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select_row(connection, identity)
            if row is None:
                raise ValueError("approval fact does not exist")
            current = _fact_from_row(row)
            if current.state is not ApprovalFactState.REQUESTED:
                return current
            abandoned = current.abandon(
                source=source,
                reason=reason,
                resolved_at=resolved_at,
            )
            updated = connection.execute(
                """
                UPDATE approval_facts
                   SET state = ?, version = ?, outcome_kind = ?,
                       outcome_fingerprint = ?, outcome_amendment_operation = ?,
                       outcome_amendment_fingerprint = ?, outcome_source = ?,
                       outcome_reason = ?, resolved_at = ?
                 WHERE session_id = ? AND run_id = ? AND approval_id = ?
                   AND action_id = ? AND state = 'requested' AND version = 0
                """,
                _outcome_values(abandoned)
                + self._identity_values(identity),
            )
            if updated.rowcount != 1:
                raise ValueError("approval fact changed before abandon")
            connection.commit()
            return abandoned
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _find(self, identity: ApprovalIdentity) -> ApprovalFact | None:
        """同步读取审批事实。"""
        connection = self._connect()
        try:
            row = self._select_row(connection, identity)
            return _fact_from_row(row) if row is not None else None
        finally:
            connection.close()

    @staticmethod
    def _identity_values(identity: ApprovalIdentity) -> tuple[str, str, str, str]:
        """返回数据库主键列值。"""
        return (
            identity.session_id,
            identity.run_id,
            identity.approval_id,
            identity.action_id,
        )

    @staticmethod
    def _select_row(
        connection: sqlite3.Connection,
        identity: ApprovalIdentity,
    ) -> sqlite3.Row | None:
        """按完整主键读取一行事实。"""
        return connection.execute(
            """
            SELECT * FROM approval_facts
             WHERE session_id = ? AND run_id = ? AND approval_id = ?
               AND action_id = ?
            """,
            SQLiteApprovalFactStore._identity_values(identity),
        ).fetchone()


class InMemoryApprovalFactStore:
    """提供测试和无持久化场景使用的异步审批事实存储。"""

    def __init__(self) -> None:
        """创建空的审批事实表。"""
        self._facts: dict[ApprovalIdentity, ApprovalFact] = {}
        self._lock = asyncio.Lock()

    async def record_requested(self, action: ApprovalAction) -> ApprovalFact:
        """记录或幂等返回一次 requested 事实。"""
        requested = ApprovalFact.requested(action)
        async with self._lock:
            current = self._facts.get(requested.identity)
            if current is None:
                self._facts[requested.identity] = requested
                return requested
            if (
                current.action_kind is not requested.action_kind
                or current.action_fingerprint != requested.action_fingerprint
            ):
                raise ValueError("approval identity conflicts with persisted action")
            return current

    async def resolve(
        self,
        identity: ApprovalIdentity,
        decision: ApprovalDecision,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> ApprovalFact:
        """以锁保护的 CAS 规则提交审批决定。"""
        async with self._lock:
            current = self._require(identity)
            if current.state is not ApprovalFactState.REQUESTED:
                return _same_or_reject_terminal(current, decision)
            resolved = current.resolve(
                decision,
                source=source,
                reason=reason,
                resolved_at=resolved_at,
            )
            self._facts[identity] = resolved
            return resolved

    async def abandon(
        self,
        identity: ApprovalIdentity,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
        resolved_at: float,
    ) -> ApprovalFact:
        """以锁保护的 CAS 规则提交 abandoned。"""
        async with self._lock:
            current = self._require(identity)
            if current.state is not ApprovalFactState.REQUESTED:
                return current
            abandoned = current.abandon(
                source=source,
                reason=reason,
                resolved_at=resolved_at,
            )
            self._facts[identity] = abandoned
            return abandoned

    async def find(self, identity: ApprovalIdentity) -> ApprovalFact | None:
        """读取审批事实快照。"""
        async with self._lock:
            return self._facts.get(identity)

    def _require(self, identity: ApprovalIdentity) -> ApprovalFact:
        """读取存在的审批事实。"""
        current = self._facts.get(identity)
        if current is None:
            raise ValueError("approval fact does not exist")
        return current


def _fact_from_row(row: sqlite3.Row) -> ApprovalFact:
    """把 SQLite 行恢复为领域事实。"""
    outcome_kind = row["outcome_kind"]
    outcome = None
    if outcome_kind is not None:
        amendment = None
        amendment_operation = row["outcome_amendment_operation"]
        amendment_fingerprint = row["outcome_amendment_fingerprint"]
        if amendment_operation is not None:
            if amendment_fingerprint is None:
                raise ValueError("persisted approval amendment is incomplete")
            amendment = ApprovalAmendment(
                operation=AmendmentOperation(str(amendment_operation)),
                action_fingerprint=ActionFingerprint(str(amendment_fingerprint)),
            )
        outcome = ApprovalOutcome(
            decision=ApprovalDecision(
                kind=ApprovalDecisionKind(str(outcome_kind)),
                action_fingerprint=ActionFingerprint(
                    str(row["outcome_fingerprint"])
                ),
                amendment=amendment,
            ),
            source=ApprovalDecisionSource(str(row["outcome_source"])),
            reason=ApprovalResolutionReason(str(row["outcome_reason"])),
            fact_version=int(row["version"]),
            resolved_at=float(row["resolved_at"]),
        )
    return ApprovalFact(
        identity=ApprovalIdentity(
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            approval_id=str(row["approval_id"]),
            action_id=str(row["action_id"]),
        ),
        action_kind=ApprovalActionKind(str(row["action_kind"])),
        action_fingerprint=ActionFingerprint(str(row["action_fingerprint"])),
        state=ApprovalFactState(str(row["state"])),
        version=int(row["version"]),
        outcome=outcome,
    )


def _outcome_values(fact: ApprovalFact) -> tuple[object, ...]:
    """返回 UPDATE 语句需要的终态字段。"""
    outcome = fact.outcome
    if outcome is None:
        raise ValueError("terminal approval fact must have an outcome")
    amendment = outcome.decision.amendment
    return (
        fact.state.value,
        fact.version,
        outcome.decision.kind.value,
        outcome.decision.action_fingerprint.value,
        amendment.operation.value if amendment is not None else None,
        (
            amendment.action_fingerprint.value
            if amendment is not None
            else None
        ),
        outcome.source.value,
        outcome.reason.value,
        outcome.resolved_at,
    )


def _same_or_reject_terminal(
    current: ApprovalFact,
    decision: ApprovalDecision,
) -> ApprovalFact:
    """允许同决定重试读取原事实，拒绝覆盖已有终态。"""
    outcome = current.outcome
    if outcome is not None and outcome.decision == decision:
        return current
    raise ValueError("approval fact is already terminal")


if __name__ == '__main__':
    pass
