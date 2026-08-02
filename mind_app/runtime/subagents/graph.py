# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
import asyncio
import sqlite3
from pathlib import Path
from engine.observability import observe_exception
from mind_core.permissions import PermissionSettings
from mind_nova.requests.permissions import (
    normalize_approval_policy,
    normalize_sandbox_mode
)
from mind_app.paths import agent_graph_db_path
from mind_app.runtime.execution import AgentContext
from mind_app.runtime.subagents.context import ForkContextSnapshot
from mind_app.runtime.subagents.control import (
    AGENT_GRAPH_SCHEMA_VERSION,
    AgentGraphCheckpoint,
    AgentGraphRecord,
    AgentSubmission
)
from mind_app.runtime.subagents.thread import AgentThreadContext

TABLE_AGENT_GRAPH_CHECKPOINTS = "agent_graph_checkpoints"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_AGENT_GRAPH_CHECKPOINTS} (
    root_session_id TEXT PRIMARY KEY,
    revision        INTEGER NOT NULL,
    updated_at_ms   INTEGER NOT NULL,
    schema_version  INTEGER NOT NULL,
    payload         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_graph_checkpoints_updated
ON {TABLE_AGENT_GRAPH_CHECKPOINTS} (updated_at_ms DESC);
"""


class AgentGraphStore:
    """持久化每个根会话的最新执行树快照。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or agent_graph_db_path()).expanduser()

    def save(self, checkpoint: AgentGraphCheckpoint) -> bool:
        """原子保存更新的 revision，忽略过期快照。"""
        payload = json.dumps(
            _checkpoint_payload(checkpoint),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection = self._connect()
        try:
            with connection:
                self._init_schema(connection)
                cursor = connection.execute(
                    f"""
                    INSERT INTO {TABLE_AGENT_GRAPH_CHECKPOINTS} (
                        root_session_id,
                        revision,
                        updated_at_ms,
                        schema_version,
                        payload
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(root_session_id) DO UPDATE SET
                        revision = excluded.revision,
                        updated_at_ms = excluded.updated_at_ms,
                        schema_version = excluded.schema_version,
                        payload = excluded.payload
                    WHERE excluded.revision > revision
                    """,
                    (
                        checkpoint.root_session_id,
                        checkpoint.revision,
                        checkpoint.updated_at_ms,
                        checkpoint.schema_version,
                        payload,
                    ),
                )
                return cursor.rowcount > 0
        finally:
            connection.close()

    def load(self, root_session_id: str) -> AgentGraphCheckpoint | None:
        """读取指定根会话的最新快照。"""
        normalized = str(root_session_id or "").strip()
        if not normalized:
            raise ValueError("agent graph root session id is required")

        connection = self._connect()
        try:
            with connection:
                self._init_schema(connection)
                row = connection.execute(
                    f"""
                    SELECT payload
                    FROM {TABLE_AGENT_GRAPH_CHECKPOINTS}
                    WHERE root_session_id = ?
                    """,
                    (normalized,),
                ).fetchone()
        finally:
            connection.close()

        if row is None:
            return None
        return _checkpoint_from_payload(json.loads(str(row["payload"])))

    def _connect(self) -> sqlite3.Connection:
        """建立执行树存储连接。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _init_schema(connection: sqlite3.Connection) -> None:
        """初始化执行树存储结构。"""
        connection.executescript(SCHEMA_SQL)


class AgentGraphPersistence:
    """将控制层 checkpoint 合并后串行写入本地存储。"""

    def __init__(self, store: AgentGraphStore) -> None:
        self._store = store
        self._pending: dict[str, AgentGraphCheckpoint] = {}
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    def publish(self, checkpoint: AgentGraphCheckpoint) -> None:
        """非阻塞提交快照，并按根会话保留最新 revision。"""
        if self._closed:
            return None
        current = self._pending.get(checkpoint.root_session_id)
        if current is None or checkpoint.revision > current.revision:
            self._pending[checkpoint.root_session_id] = checkpoint
        self._wake.set()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(),
                name="agent graph persistence",
            )

    async def close(self) -> None:
        """刷新待写快照并停止单写者。"""
        if self._closed:
            if self._worker is not None:
                await asyncio.gather(self._worker, return_exceptions=False)
            return None
        self._closed = True
        self._wake.set()
        if self._worker is not None:
            await asyncio.gather(self._worker, return_exceptions=False)

    async def _run(self) -> None:
        """按 revision 串行刷新各根会话的最新快照。"""
        while True:
            await self._wake.wait()
            self._wake.clear()
            batch = tuple(self._pending.values())
            self._pending.clear()

            for checkpoint in batch:
                await self._save(checkpoint)

            if self._closed and not self._pending:
                return None

    async def _save(self, checkpoint: AgentGraphCheckpoint) -> None:
        """在工作线程中保存快照并隔离存储故障。"""
        try:
            await asyncio.to_thread(self._store.save, checkpoint)
        except (OSError, TypeError, ValueError, sqlite3.Error) as error:
            observe_exception(
                "subagent.graph.save_failed",
                error,
                level="WARNING",
                root_session_id=checkpoint.root_session_id,
                revision=checkpoint.revision,
            )


def _checkpoint_payload(checkpoint: AgentGraphCheckpoint) -> dict[str, typing.Any]:
    """把执行树快照转换为稳定存储载荷。"""
    if not isinstance(checkpoint, AgentGraphCheckpoint):
        raise TypeError("agent graph checkpoint is required")
    return {
        "schema_version": checkpoint.schema_version,
        "root_session_id": checkpoint.root_session_id,
        "revision": checkpoint.revision,
        "updated_at_ms": checkpoint.updated_at_ms,
        "records": [_record_payload(record) for record in checkpoint.records],
    }


def _record_payload(record: AgentGraphRecord) -> dict[str, typing.Any]:
    """把单个执行主体快照转换为存储载荷。"""
    thread = record.thread
    agent  = thread.agent
    fork   = thread.fork_context

    return {
        "thread": {
            "agent": {
                "agent_id": agent.agent_id,
                "agent_type": agent.agent_type,
                "root_session_id": agent.root_session_id,
                "task_name": agent.task_name,
                "task_path": agent.task_path,
                "parent_agent_id": agent.parent_agent_id,
                "depth": agent.depth,
            },
            "cid": thread.cid,
            "sid": thread.sid,
            "source": thread.source,
            "cwd": thread.cwd,
            "permissions": {
                "sandbox_mode": thread.permissions.sandbox_mode,
                "approval_policy": thread.permissions.approval_policy,
            },
            "pref_config": thread.config_snapshot(),
            "spawn_turn_id": thread.spawn_turn_id,
            "fork_turns": thread.fork_turns,
            "fork_context": {
                "requested_turns": fork.requested_turns,
                "parts": list(fork.parts),
                "available_turns": fork.available_turns,
                "selected_turns": fork.selected_turns,
                "included_turns": fork.included_turns,
                "chars": fork.chars,
                "truncated": fork.truncated,
            },
            "transcript_path": thread.transcript_path,
            "skills": thread.skills_snapshot(),
        },
        "status": record.status,
        "submission": _submission_payload(record.submission),
        "queue": [_submission_payload(item) for item in record.queue],
        "turn_count": record.turn_count,
        "error": record.error,
        "status_before_close": record.status_before_close,
    }


def _submission_payload(
    submission: AgentSubmission | None,
) -> dict[str, typing.Any] | None:
    """把任务载荷转换为可序列化对象。"""
    if submission is None:
        return None
    return {
        "submission_id": submission.submission_id,
        "message": submission.message,
        "kind": submission.kind,
        "created_at_ms": submission.created_at_ms,
        "parent_turn_id": submission.parent_turn_id,
    }


def _checkpoint_from_payload(payload: typing.Any) -> AgentGraphCheckpoint:
    """校验存储载荷并还原执行树快照。"""
    data = _mapping(payload, "agent graph checkpoint")

    schema_version = _positive_int(data.get("schema_version"), "schema version")
    if schema_version != AGENT_GRAPH_SCHEMA_VERSION:
        raise ValueError(f"unsupported agent graph schema: {schema_version}")

    records = data.get("records")
    if not isinstance(records, list):
        raise TypeError("agent graph records must be a list")

    return AgentGraphCheckpoint(
        root_session_id=_required_text(data.get("root_session_id"), "root session id"),
        revision=_positive_int(data.get("revision"), "revision"),
        updated_at_ms=_positive_int(data.get("updated_at_ms"), "updated timestamp"),
        records=tuple(_record_from_payload(item) for item in records),
        schema_version=schema_version,
    )


def _record_from_payload(payload: typing.Any) -> AgentGraphRecord:
    """校验并还原单个执行主体快照。"""
    data = _mapping(payload, "agent graph record")

    status = _required_text(data.get("status"), "agent status")
    if status not in {
        "pending", "running", "completed", "failed", "interrupted", "closed",
    }:
        raise ValueError("agent graph status is invalid")

    resume_status = data.get("status_before_close")
    if resume_status not in {None, "completed", "failed", "interrupted"}:
        raise ValueError("agent graph resume status is invalid")

    queue = data.get("queue")
    if not isinstance(queue, list):
        raise TypeError("agent graph queue must be a list")

    return AgentGraphRecord(
        thread=_thread_from_payload(data.get("thread")),
        status=typing.cast(typing.Any, status),
        submission=_submission_from_payload(data.get("submission")),
        queue=tuple(
            typing.cast(AgentSubmission, _submission_from_payload(item))
            for item in queue
        ),
        turn_count=_nonnegative_int(data.get("turn_count"), "turn count"),
        error=str(data.get("error") or ""),
        status_before_close=typing.cast(typing.Any, resume_status),
    )


def _thread_from_payload(payload: typing.Any) -> AgentThreadContext:
    """校验并还原执行线程的固定上下文。"""
    data            = _mapping(payload, "agent thread")
    agent_data      = _mapping(data.get("agent"), "agent context")
    permission_data = _mapping(data.get("permissions"), "agent permissions")
    fork_data       = _mapping(data.get("fork_context"), "fork context")
    skills          = data.get("skills")
    pref_config     = data.get("pref_config")

    if not isinstance(skills, list) or any(not isinstance(item, dict) for item in skills):
        raise TypeError("agent skills must be a list of objects")
    if not isinstance(pref_config, dict):
        raise TypeError("agent preference config must be an object")

    parent_agent_id = agent_data.get("parent_agent_id")
    if parent_agent_id is not None and not isinstance(parent_agent_id, str):
        raise TypeError("agent parent id must be a string or null")

    parts = fork_data.get("parts")
    if not isinstance(parts, list) or any(not isinstance(item, str) for item in parts):
        raise TypeError("fork context parts must be a list of strings")

    agent = AgentContext(
        agent_id=_required_text(agent_data.get("agent_id"), "agent id"),
        agent_type=_required_text(agent_data.get("agent_type"), "agent type"),
        root_session_id=_required_text(
            agent_data.get("root_session_id"),
            "agent root session id",
        ),
        task_name=_required_text(agent_data.get("task_name"), "agent task name"),
        task_path=_required_text(agent_data.get("task_path"), "agent task path"),
        parent_agent_id=parent_agent_id,
        depth=_nonnegative_int(agent_data.get("depth"), "agent depth"),
    )
    fork_context = ForkContextSnapshot(
        requested_turns=_required_text(
            fork_data.get("requested_turns"),
            "fork requested turns",
        ),
        parts=tuple(parts),
        available_turns=_nonnegative_int(
            fork_data.get("available_turns"),
            "fork available turns",
        ),
        selected_turns=_nonnegative_int(
            fork_data.get("selected_turns"),
            "fork selected turns",
        ),
        included_turns=_nonnegative_int(
            fork_data.get("included_turns"),
            "fork included turns",
        ),
        chars=_nonnegative_int(fork_data.get("chars"), "fork characters"),
        truncated=_boolean(fork_data.get("truncated"), "fork truncated"),
    )

    return AgentThreadContext(
        agent=agent,
        cid=_required_text(data.get("cid"), "agent cid"),
        sid=_required_text(data.get("sid"), "agent sid"),
        source=_required_text(data.get("source"), "agent source"),
        cwd=_required_text(data.get("cwd"), "agent cwd"),
        permissions=PermissionSettings(
            sandbox_mode=normalize_sandbox_mode(
                permission_data.get("sandbox_mode")
            ),
            approval_policy=normalize_approval_policy(
                permission_data.get("approval_policy")
            ),
        ),
        pref_config=pref_config,
        spawn_turn_id=_required_text(data.get("spawn_turn_id"), "spawn turn id"),
        fork_turns=_required_text(data.get("fork_turns"), "fork turns"),
        fork_context=fork_context,
        transcript_path=str(data.get("transcript_path") or ""),
        skills=tuple(skills),
    )


def _submission_from_payload(payload: typing.Any) -> AgentSubmission | None:
    """校验并还原一项可排队任务。"""
    if payload is None:
        return None

    data = _mapping(payload, "agent submission")
    kind = _required_text(data.get("kind"), "submission kind")

    return AgentSubmission(
        submission_id=_required_text(
            data.get("submission_id"),
            "submission id",
        ),
        message=_required_text(data.get("message"), "submission message"),
        kind=typing.cast(typing.Any, kind),
        created_at_ms=_positive_int(
            data.get("created_at_ms"),
            "submission timestamp",
        ),
        parent_turn_id=str(data.get("parent_turn_id") or ""),
    )


def _mapping(value: typing.Any, label: str) -> dict[str, typing.Any]:
    """返回经校验的映射副本。"""
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return dict(value)


def _required_text(value: typing.Any, label: str) -> str:
    """返回经校验的非空文本。"""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _positive_int(value: typing.Any, label: str) -> int:
    """返回经校验的正整数。"""
    normalized = _nonnegative_int(value, label)
    if normalized == 0:
        raise ValueError(f"{label} must be positive")
    return normalized


def _nonnegative_int(value: typing.Any, label: str) -> int:
    """返回经校验的非负整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _boolean(value: typing.Any, label: str) -> bool:
    """返回经校验的布尔值。"""
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be a boolean")
    return value


if __name__ == "__main__":
    pass
