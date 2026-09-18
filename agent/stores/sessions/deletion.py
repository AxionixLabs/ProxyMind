# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import sqlite3
from contextlib import closing

from agent.application.config.session_identity import derive_local_session_id
from agent.ports.session_deletion import (
    LocalDeletionPlan,
    LocalDeletionRecord,
    LocalDeletionTarget,
    SessionDeletionConflict,
    TranscriptDeletionStore,
)
from agent.stores.agents.graph import AgentGraphStore
from agent.stores.approvals.facts import SQLiteApprovalFactStore
from agent.stores.effects.journal import LocalEffectJournal
from agent.stores.runs.store import SQLiteRunStore
from agent.stores.sessions.history import ConversationHistoryStore
from protocol.schema.identifiers import (
    normalize_request_id,
    valid_session_ids,
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_deletions (
    request_id TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    layout TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1))
);
"""


class SQLiteSessionDeletionStore:
    """在历史库保存不可变清理计划，协调既有 Store；不控制运行期资源或远端状态。"""

    def __init__(
        self, *, history: ConversationHistoryStore, graphs: AgentGraphStore,
        runs: SQLiteRunStore, effects: LocalEffectJournal, approvals: SQLiteApprovalFactStore,
        transcripts: TranscriptDeletionStore,
    ) -> None:
        """绑定同一状态目录下的持久化所有者，不创建长期数据库连接。"""
        self.history = history
        self.graphs = graphs
        self.runs = runs
        self.effects = effects
        self.approvals = approvals
        self.transcripts = transcripts

    def delete(self, plan: LocalDeletionPlan) -> None:
        """保存意图后逐项幂等清理，全部成功才持久标记完成。"""
        normalized = _normalize_plan(plan)
        encoded = _encode_plan(normalized)
        layout = self._layout(normalized)
        self._prepare_normalized(normalized, encoded=encoded, layout=layout)
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT completed FROM session_deletions WHERE request_id = ?",
                                     (normalized.request_id,)).fetchone()
            if row is None:
                raise SessionDeletionConflict("deletion request was not persisted")
            if row[0]:
                return
        with self.transcripts.lock_sessions(tuple(target.sid for target in normalized.targets)) as lease:
            lease.retire()
            # Effect 旧归属错误必须在清理关联索引前暴露；各 Store 自己提交停写和删除事务。
            self.effects.delete_sessions(normalized.targets)
            self.approvals.delete_sessions(normalized.targets)
            self.runs.delete_sessions(normalized.targets)
            lease.delete()
            self.graphs.delete_sessions(normalized.targets)
            self.history.delete_sessions(normalized.targets)
            with closing(self._connect()) as connection, connection:
                connection.execute("UPDATE session_deletions SET completed = 1 WHERE request_id = ?",
                                   (normalized.request_id,))

    def prepare(self, plan: LocalDeletionPlan) -> None:
        """只持久化不可变清理计划，供远端未知结果恢复查询。"""
        normalized = _normalize_plan(plan)
        self._prepare_normalized(
            normalized,
            encoded=_encode_plan(normalized),
            layout=self._layout(normalized),
        )

    def pending(self) -> tuple[LocalDeletionPlan, ...]:
        """恢复完整原始集合，不依赖已过期或已删除的历史游标和代理图。"""
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT plan, layout FROM session_deletions WHERE completed = 0 ORDER BY request_id")
            plans: list[LocalDeletionPlan] = []
            for row in rows:
                plan = _decode_plan(str(row[0]))
                if row[1] != self._layout(plan):
                    raise SessionDeletionConflict("deletion storage paths changed since the original request")
                plans.append(plan)
            return tuple(plans)

    def lookup(self, request_id: str) -> LocalDeletionRecord | None:
        """读取同一请求的持久事实，不依据当前会话重建根身份。"""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT plan, layout, completed FROM session_deletions WHERE request_id = ?",
                (normalize_request_id(request_id),),
            ).fetchone()
            if row is None:
                return None
            plan = _decode_plan(str(row[0]))
            if row[1] != self._layout(plan):
                raise SessionDeletionConflict("deletion storage paths changed since the original request")
            return LocalDeletionRecord(plan, bool(row[2]))

    def rejected(self, plan: LocalDeletionPlan) -> None:
        """释放服务端明确拒绝且范围一致的未完成意图，保留已完成事实。"""
        normalized = _normalize_plan(plan)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM session_deletions WHERE request_id = ? AND plan = ? AND layout = ? AND completed = 0",
                (plan.request_id, _encode_plan(normalized), self._layout(normalized)),
            )

    def for_session(self, cid: str, sid: str) -> LocalDeletionRecord | None:
        """查找占有指定线上身份的原始删除事实。"""
        with closing(self._connect()) as connection:
            for row in connection.execute("SELECT plan, layout, completed FROM session_deletions"):
                plan = _decode_plan(str(row[0]))
                if any((target.cid, target.sid) == (cid, sid) for target in plan.targets):
                    if row[1] != self._layout(plan):
                        raise SessionDeletionConflict("deletion storage paths changed since the original request")
                    return LocalDeletionRecord(plan, bool(row[2]))
        return None

    def _layout(self, plan: LocalDeletionPlan) -> str:
        """保存参与清理的实际存储位置，拒绝切换目录后错误地报告旧目录已清理。"""
        return json.dumps({
            "history": str(self.history.db_path.resolve()),
            "graphs": str(self.graphs.db_path.resolve()),
            "runs": str(self.runs.db_path.resolve()),
            "effects": str(self.effects.db_path.resolve()),
            "approvals": str(self.approvals.db_path.resolve()),
            "transcripts": self.transcripts.deletion_identity(tuple(target.sid for target in plan.targets)),
        }, sort_keys=True, separators=(",", ":"))

    def _connect(self) -> sqlite3.Connection:
        """复用历史库保存清理事实，FULL 同步确保下一资源清理前意图已提交。"""
        self.history.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.history.db_path, timeout=30.0)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA_SQL)
            return connection
        except BaseException:
            connection.close()
            raise

    def _prepare_normalized(
        self,
        plan: LocalDeletionPlan,
        *,
        encoded: str,
        layout: str,
    ) -> None:
        """原子登记待完成计划并校验重复请求范围不可改变。"""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            identities = {(target.cid, target.sid) for target in plan.targets}
            for row in connection.execute(
                "SELECT plan FROM session_deletions WHERE request_id != ?", (plan.request_id,),
            ):
                existing = _decode_plan(str(row[0]))
                if identities.intersection((target.cid, target.sid) for target in existing.targets):
                    raise SessionDeletionConflict("session already belongs to a deletion request")
            connection.execute(
                "INSERT OR IGNORE INTO session_deletions (request_id, plan, layout) VALUES (?, ?, ?)",
                (plan.request_id, encoded, layout),
            )
            row = connection.execute(
                "SELECT plan, layout FROM session_deletions WHERE request_id = ?",
                (plan.request_id,),
            ).fetchone()
            if row is None or row[0] != encoded or row[1] != layout:
                raise SessionDeletionConflict(
                    "deletion request scope conflicts with persisted plan"
                )


def _normalize_plan(plan: LocalDeletionPlan) -> LocalDeletionPlan:
    """验证唯一有界目标并补齐现有前端确定性身份，使尚未入账的迟到 Run 也被封锁。"""
    if normalize_request_id(plan.request_id) != plan.request_id:
        raise ValueError("deletion request id is not normalized")
    if not 1 <= len(plan.targets) <= 256:
        raise ValueError("deletion requires 1-256 targets")
    if len({target.sid for target in plan.targets}) != len(plan.targets):
        raise ValueError("duplicate deletion session")
    targets: list[LocalDeletionTarget] = []
    for target in plan.targets:
        if (not valid_session_ids(target.cid, target.sid)
                or target.cid != target.cid.strip() or target.sid != target.sid.strip()):
            raise ValueError("invalid deletion session coordinates")
        identities = set(target.local_session_ids)
        if any(not identity or identity != identity.strip() for identity in identities):
            raise ValueError("invalid local session identity")
        identities.update(derive_local_session_id(source, {"cid": target.cid, "sid": target.sid})
                          for source in ("cli", "tui"))
        targets.append(LocalDeletionTarget(target.cid, target.sid, tuple(sorted(identities))))
    root = next((target for target in targets if (target.cid, target.sid) == (plan.root.cid, plan.root.sid)), None)
    if root is None:
        raise ValueError("deletion root must be in the target set")
    return LocalDeletionPlan(plan.request_id, tuple(sorted(targets, key=lambda target: (target.cid, target.sid))), root)


def _encode_plan(plan: LocalDeletionPlan) -> str:
    """只编码恢复所需身份，不复制会话正文、配置或凭据。"""
    return json.dumps({"request_id": plan.request_id, "root": {"cid": plan.root.cid, "sid": plan.root.sid}, "targets": [
        {"cid": target.cid, "sid": target.sid, "local_session_ids": list(target.local_session_ids)}
        for target in plan.targets
    ]}, sort_keys=True, separators=(",", ":"))


def _decode_plan(encoded: str) -> LocalDeletionPlan:
    """在持久边界严格验证 JSON 后构造具名删除计划。"""
    value = json.loads(encoded)
    if not isinstance(value, dict) or set(value) != {"request_id", "root", "targets"}:
        raise ValueError("invalid persisted deletion plan")
    request_id, rows = value["request_id"], value["targets"]
    if not isinstance(request_id, str) or not isinstance(rows, list):
        raise ValueError("invalid persisted deletion identity")
    targets: list[LocalDeletionTarget] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"cid", "sid", "local_session_ids"}:
            raise ValueError("invalid persisted deletion target")
        cid, sid, identities = row["cid"], row["sid"], row["local_session_ids"]
        if not isinstance(cid, str) or not isinstance(sid, str) or not isinstance(identities, list):
            raise ValueError("invalid persisted deletion coordinates")
        local_ids: list[str] = []
        for identity in identities:
            if not isinstance(identity, str):
                raise ValueError("invalid persisted local session identity")
            local_ids.append(identity)
        targets.append(LocalDeletionTarget(cid, sid, tuple(local_ids)))
    root = value["root"]
    if (not isinstance(root, dict) or set(root) != {"cid", "sid"}
            or not isinstance(root["cid"], str) or not isinstance(root["sid"], str)):
        raise ValueError("invalid persisted deletion root")
    plan = _normalize_plan(LocalDeletionPlan(
        request_id, tuple(targets), LocalDeletionTarget(root["cid"], root["sid"], ()),
    ))
    if _encode_plan(plan) != encoded:
        raise ValueError("persisted deletion plan is not canonical")
    return plan


if __name__ == '__main__':
    pass
