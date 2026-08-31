# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from observability import observe_exception
from agent.application import PermissionSettings
from agent.application import AgentThreadContext
from agent.domain.agents import (
    AgentResumeStatus,
    AgentStatus,
    AgentSubmission,
    AgentSubmissionKind,
    FINAL_AGENT_STATUSES,
)
from protocol.schema.permissions import (
    normalize_approval_policy,
    normalize_approval_reviewer,
    normalize_network_access,
    normalize_sandbox_mode,
)
from infrastructure.config.runtime_paths import agent_graph_db_path
from agent.application.execution import AgentContext
from agent.application import ForkContextSnapshot
from .agent_mailbox import (
    AgentMailboxEvent,
    AgentMailboxEventKind,
    AgentMailboxSnapshot,
)

TABLE_AGENT_GRAPH_CHECKPOINTS = "agent_graph_checkpoints"

DEFAULT_AGENT_GRAPH_TTL_MS = 24 * 60 * 60 * 1000
DEFAULT_AGENT_GRAPH_LIMIT  = 200

__all__ = (
    "AgentGraphCheckpoint",
    "AgentGraphPersistence",
    "AgentGraphPersistenceError",
    "AgentGraphRecord",
    "AgentGraphStore",
)

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_AGENT_GRAPH_CHECKPOINTS} (
    root_session_id TEXT PRIMARY KEY,
    revision        INTEGER NOT NULL,
    updated_at_ms   INTEGER NOT NULL,
    payload         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_graph_checkpoints_updated
ON {TABLE_AGENT_GRAPH_CHECKPOINTS} (updated_at_ms DESC);
"""


@dataclass(frozen=True, slots=True)
class AgentGraphRecord:
    """保存可用于重建单个执行主体的控制快照。"""

    thread: AgentThreadContext
    status: AgentStatus
    submission: AgentSubmission | None = None
    queue: tuple[AgentSubmission, ...] = ()
    turn_count: int = 0
    error: str = ""
    status_before_close: AgentResumeStatus | None = None

    def __post_init__(self) -> None:
        """校验执行主体快照的持久化字段。"""
        if not isinstance(self.thread, AgentThreadContext):
            raise TypeError("agent graph thread is required")
        if self.status not in FINAL_AGENT_STATUSES | {"pending", "running"}:
            raise ValueError("agent graph status is invalid")
        if self.submission is not None and not isinstance(
            self.submission,
            AgentSubmission,
        ):
            raise TypeError("agent graph submission is invalid")
        if not isinstance(self.queue, tuple) or any(
            not isinstance(item, AgentSubmission)
            for item in self.queue
        ):
            raise TypeError("agent graph queue is invalid")
        if any(item.kind != "followup" for item in self.queue):
            raise ValueError("agent graph queue requires followup submissions")
        if (
            isinstance(self.turn_count, bool)
            or not isinstance(self.turn_count, int)
            or self.turn_count < 0
        ):
            raise ValueError("agent graph turn count must be non-negative")
        if self.status_before_close not in {
            None,
            "completed",
            "failed",
            "interrupted",
            "interrupted_by_restart",
        }:
            raise ValueError("agent graph resume status is invalid")
        object.__setattr__(self, "error", str(self.error or ""))


@dataclass(frozen=True, slots=True)
class AgentGraphCheckpoint:
    """保存单个根会话执行树与邮箱的完整快照。"""

    root_session_id: str
    revision: int
    updated_at_ms: int
    records: tuple[AgentGraphRecord, ...] = ()
    mailbox: AgentMailboxSnapshot = AgentMailboxSnapshot.empty()

    def __post_init__(self) -> None:
        """校验执行树快照的顺序、根会话和邮箱。"""
        root_session_id = str(self.root_session_id or "").strip()
        if not root_session_id:
            raise ValueError("agent graph root session id is required")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision <= 0
        ):
            raise ValueError("agent graph revision must be positive")
        if (
            isinstance(self.updated_at_ms, bool)
            or not isinstance(self.updated_at_ms, int)
            or self.updated_at_ms <= 0
        ):
            raise ValueError("agent graph timestamp must be positive")
        if not isinstance(self.records, tuple) or any(
            not isinstance(record, AgentGraphRecord)
            for record in self.records
        ):
            raise TypeError("agent graph records must be a tuple")

        agent_ids = [record.thread.agent.agent_id for record in self.records]
        task_paths = [record.thread.agent.task_path for record in self.records]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("agent graph agent ids must be unique")
        if len(task_paths) != len(set(task_paths)):
            raise ValueError("agent graph task paths must be unique")
        if any(
            record.thread.agent.root_session_id != root_session_id
            for record in self.records
        ):
            raise ValueError("agent graph record belongs to another root session")
        if not isinstance(self.mailbox, AgentMailboxSnapshot):
            raise TypeError("agent graph mailbox snapshot is required")

        identities = {
            "root": "/root",
            **{
                record.thread.agent.agent_id: record.thread.agent.task_path
                for record in self.records
            },
        }
        for event in self.mailbox.events:
            if identities.get(event.source_agent_id) != event.source_task_path:
                raise ValueError("mailbox event source is outside the agent graph")
            if event.kind == "message" and identities.get(
                event.recipient_agent_id
            ) != event.recipient_task_path:
                raise ValueError("mailbox recipient is outside the agent graph")
        object.__setattr__(self, "root_session_id", root_session_id)


class AgentGraphStore:
    """持久化每个根会话的最新执行树快照。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        ttl_ms: int = DEFAULT_AGENT_GRAPH_TTL_MS,
        max_items: int = DEFAULT_AGENT_GRAPH_LIMIT,
    ) -> None:
        _require_positive_integer(ttl_ms, "agent graph ttl")
        _require_positive_integer(max_items, "agent graph item limit")

        self.db_path   = Path(db_path or agent_graph_db_path()).expanduser()
        self.ttl_ms    = ttl_ms
        self.max_items = max_items

    def save(
        self,
        checkpoint: AgentGraphCheckpoint,
        *,
        now_ms: int | None = None,
    ) -> bool:
        """原子保存更新的 revision，忽略过期快照。"""
        if not isinstance(checkpoint, AgentGraphCheckpoint):
            raise TypeError("agent graph checkpoint is required")
        now = _normalize_now_ms(now_ms)
        if checkpoint.updated_at_ms <= now - self.ttl_ms:
            return False
        payload = json.dumps(
            _checkpoint_payload(checkpoint),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection = self._connect()
        try:
            with connection:
                self._init_schema(connection)
                self._prune_expired(connection, now_ms=now)
                cursor = connection.execute(
                    f"""
                    INSERT INTO {TABLE_AGENT_GRAPH_CHECKPOINTS} (
                        root_session_id,
                        revision,
                        updated_at_ms,
                        payload
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(root_session_id) DO UPDATE SET
                        revision = excluded.revision,
                        updated_at_ms = excluded.updated_at_ms,
                        payload = excluded.payload
                    WHERE excluded.revision > revision
                    """,
                    (
                        checkpoint.root_session_id,
                        checkpoint.revision,
                        checkpoint.updated_at_ms,
                        payload,
                    ),
                )
                self._trim(connection)
                return cursor.rowcount > 0
        finally:
            connection.close()

    def load(
        self,
        root_session_id: str,
        *,
        now_ms: int | None = None,
    ) -> AgentGraphCheckpoint | None:
        """读取指定根会话的最新快照。"""
        normalized = str(root_session_id or "").strip()
        if not normalized:
            raise ValueError("agent graph root session id is required")
        now = _normalize_now_ms(now_ms)

        connection = self._connect()
        try:
            with connection:
                self._init_schema(connection)
                self._prune_expired(connection, now_ms=now)
                self._trim(connection)
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

    def prune(self, *, now_ms: int | None = None) -> int:
        """删除过期及超出数量上限的根会话快照。"""
        now = _normalize_now_ms(now_ms)
        connection = self._connect()
        try:
            with connection:
                self._init_schema(connection)
                before = connection.total_changes
                self._prune_expired(connection, now_ms=now)
                self._trim(connection)
                return connection.total_changes - before
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        """建立执行树存储连接。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _init_schema(connection: sqlite3.Connection) -> None:
        """初始化执行树存储结构。"""
        columns = {
            str(row[1])
            for row in connection.execute(
                f"PRAGMA table_info({TABLE_AGENT_GRAPH_CHECKPOINTS})"
            )
        }
        expected = {
            "root_session_id",
            "revision",
            "updated_at_ms",
            "payload",
        }
        if columns and columns != expected:
            connection.execute(f"DROP TABLE {TABLE_AGENT_GRAPH_CHECKPOINTS}")
        connection.executescript(SCHEMA_SQL)

    def _prune_expired(
        self,
        connection: sqlite3.Connection,
        *,
        now_ms: int,
    ) -> None:
        """删除已经超过恢复窗口的执行树快照。"""
        connection.execute(
            f"""
            DELETE FROM {TABLE_AGENT_GRAPH_CHECKPOINTS}
            WHERE updated_at_ms <= ?
            """,
            (now_ms - self.ttl_ms,),
        )

    def _trim(self, connection: sqlite3.Connection) -> None:
        """只保留最近更新的有限数量执行树。"""
        connection.execute(
            f"""
            DELETE FROM {TABLE_AGENT_GRAPH_CHECKPOINTS}
            WHERE root_session_id NOT IN (
                SELECT root_session_id
                FROM {TABLE_AGENT_GRAPH_CHECKPOINTS}
                ORDER BY updated_at_ms DESC, root_session_id ASC
                LIMIT ?
            )
            """,
            (self.max_items,),
        )


class AgentGraphPersistenceError(RuntimeError):
    """表示最新执行树快照未能持久化。"""


class AgentGraphPersistence:
    """将控制层 checkpoint 合并后串行写入本地存储。"""

    def __init__(self, store: AgentGraphStore) -> None:
        self._store = store
        self._pending: dict[str, AgentGraphCheckpoint] = {}
        self._failed: dict[
            str,
            tuple[AgentGraphCheckpoint, Exception],
        ] = {}
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    def publish(self, checkpoint: AgentGraphCheckpoint) -> None:
        """非阻塞提交快照，并按根会话保留最新 revision。"""
        if self._closed:
            return None

        root_session_id = checkpoint.root_session_id

        current = self._pending.get(root_session_id)

        failed = self._failed.get(root_session_id)
        if failed is not None and (
            current is None
            or failed[0].revision > current.revision
        ):
            current = failed[0]
        if current is None or checkpoint.revision > current.revision:
            self._pending[root_session_id] = checkpoint
            self._failed.pop(root_session_id, None)
        elif root_session_id in self._failed:
            self._pending[root_session_id] = current
            self._failed.pop(root_session_id, None)
        self._idle.clear()
        self._wake.set()
        self._ensure_worker()

    async def flush(self) -> None:
        """等待当前快照落盘，并对失败 revision 做一次受控重试。"""
        await self._idle.wait()
        if self._failed:
            retry = tuple(
                checkpoint
                for checkpoint, _error in self._failed.values()
            )
            self._failed.clear()
            for checkpoint in retry:
                current = self._pending.get(checkpoint.root_session_id)
                if current is None or checkpoint.revision > current.revision:
                    self._pending[checkpoint.root_session_id] = checkpoint
            self._idle.clear()
            self._wake.set()
            self._ensure_worker()
            await self._idle.wait()
        self._raise_failed()

    async def close(self) -> None:
        """刷新待写快照并停止单写者。"""
        if self._closed:
            if self._worker is not None:
                await asyncio.gather(self._worker, return_exceptions=False)
            self._raise_failed()
            return None
        failure: AgentGraphPersistenceError | None = None
        try:
            await self.flush()
        except AgentGraphPersistenceError as error:
            failure = error
        finally:
            self._closed = True
            self._wake.set()
            if self._worker is not None:
                await asyncio.gather(self._worker, return_exceptions=False)
        if failure is not None:
            raise failure

    async def _run(self) -> None:
        """按 revision 串行刷新各根会话的最新快照。"""
        while True:
            await self._wake.wait()
            self._wake.clear()
            batch = self._pending
            self._pending = {}

            for checkpoint in batch.values():
                error   = await self._save(checkpoint)
                current = self._pending.get(checkpoint.root_session_id)

                if error is not None and (
                    current is None
                    or current.revision <= checkpoint.revision
                ):
                    self._failed[checkpoint.root_session_id] = (
                        checkpoint,
                        error,
                    )

            if not self._pending:
                self._idle.set()
                if self._closed:
                    return None

    async def _save(
        self,
        checkpoint: AgentGraphCheckpoint,
    ) -> Exception | None:
        """在工作线程中保存快照并返回可重试故障。"""
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
            return error
        return None

    def _ensure_worker(self) -> None:
        """确保串行持久化工作协程正在运行。"""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(),
                name="agent graph persistence",
            )

    def _raise_failed(self) -> None:
        """在仍有失败 revision 时抛出聚合边界错误。"""
        if not self._failed:
            return None
        roots = ", ".join(sorted(self._failed))
        raise AgentGraphPersistenceError(
            f"agent graph persistence failed: {roots}"
        )


def _checkpoint_payload(checkpoint: AgentGraphCheckpoint) -> dict[str, typing.Any]:
    """把执行树快照转换为稳定存储载荷。"""
    if not isinstance(checkpoint, AgentGraphCheckpoint):
        raise TypeError("agent graph checkpoint is required")
    return {
        "root_session_id": checkpoint.root_session_id,
        "revision": checkpoint.revision,
        "updated_at_ms": checkpoint.updated_at_ms,
        "records": [_record_payload(record) for record in checkpoint.records],
        "mailbox": _mailbox_payload(checkpoint.mailbox),
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
                "approvals_reviewer": thread.permissions.approvals_reviewer,
                "network_access": thread.permissions.network_access,
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
            "parent_transcript_path": thread.parent_transcript_path,
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


def _mailbox_payload(snapshot: AgentMailboxSnapshot) -> dict[str, typing.Any]:
    """把邮箱事件和消费位置转换为存储载荷。"""
    return {
        "sequence": snapshot.sequence,
        "events": [event.to_dict() for event in snapshot.events],
        "consumed": [
            {
                "reader_agent_id": reader,
                "event_ids": list(event_ids),
            }
            for reader, event_ids in snapshot.consumed
        ],
    }


def _checkpoint_from_payload(payload: typing.Any) -> AgentGraphCheckpoint:
    """校验存储载荷并还原执行树快照。"""
    data = _mapping(payload, "agent graph checkpoint")

    records = data.get("records")
    if not isinstance(records, list):
        raise TypeError("agent graph records must be a list")

    return AgentGraphCheckpoint(
        root_session_id=_required_text(data.get("root_session_id"), "root session id"),
        revision=_positive_int(data.get("revision"), "revision"),
        updated_at_ms=_positive_int(data.get("updated_at_ms"), "updated timestamp"),
        records=tuple(_record_from_payload(item) for item in records),
        mailbox=_mailbox_from_payload(data.get("mailbox")),
    )


def _agent_status(value: typing.Any) -> AgentStatus:
    """校验并收窄持久化的执行主体状态。"""
    status = _required_text(value, "agent status")
    match status:
        case "pending":
            return "pending"
        case "running":
            return "running"
        case "completed":
            return "completed"
        case "failed":
            return "failed"
        case "interrupted":
            return "interrupted"
        case "interrupted_by_restart":
            return "interrupted_by_restart"
        case "closed":
            return "closed"
        case _:
            raise ValueError("agent graph status is invalid")


def _agent_resume_status(value: typing.Any) -> AgentResumeStatus | None:
    """校验并收窄关闭前的可恢复状态。"""
    if value is None:
        return None
    status = _required_text(value, "agent resume status")
    match status:
        case "completed":
            return "completed"
        case "failed":
            return "failed"
        case "interrupted":
            return "interrupted"
        case "interrupted_by_restart":
            return "interrupted_by_restart"
        case _:
            raise ValueError("agent graph resume status is invalid")


def _submission_kind(value: typing.Any) -> AgentSubmissionKind:
    """校验并收窄任务提交类型。"""
    kind = _required_text(value, "submission kind")
    if kind == "initial":
        return "initial"
    if kind == "followup":
        return "followup"
    raise ValueError("agent submission kind is invalid")


def _mailbox_event_kind(value: typing.Any) -> AgentMailboxEventKind:
    """校验并收窄邮箱事件类型。"""
    kind = _required_text(value, "mailbox event kind")
    if kind == "message":
        return "message"
    if kind == "queue":
        return "queue"
    if kind == "status":
        return "status"
    raise ValueError("mailbox event kind is invalid")


def _record_from_payload(payload: typing.Any) -> AgentGraphRecord:
    """校验并还原单个执行主体快照。"""
    data = _mapping(payload, "agent graph record")

    status = _agent_status(data.get("status"))
    resume_status = _agent_resume_status(data.get("status_before_close"))

    queue = data.get("queue")
    if not isinstance(queue, list):
        raise TypeError("agent graph queue must be a list")

    return AgentGraphRecord(
        thread=_thread_from_payload(data.get("thread")),
        status=status,
        submission=_submission_from_payload(data.get("submission")),
        queue=tuple(
            _submission_from_payload(item)
            for item in queue
        ),
        turn_count=_nonnegative_int(data.get("turn_count"), "turn count"),
        error=str(data.get("error") or ""),
        status_before_close=resume_status,
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
            approvals_reviewer=normalize_approval_reviewer(
                permission_data.get("approvals_reviewer")
            ),
            network_access=normalize_network_access(
                permission_data.get("network_access")
            ),
        ),
        pref_config=pref_config,
        spawn_turn_id=_required_text(data.get("spawn_turn_id"), "spawn turn id"),
        fork_turns=_required_text(data.get("fork_turns"), "fork turns"),
        fork_context=fork_context,
        transcript_path=str(data.get("transcript_path") or ""),
        parent_transcript_path=str(data.get("parent_transcript_path") or ""),
        skills=tuple(skills),
    )


def _submission_from_payload(payload: typing.Any) -> AgentSubmission | None:
    """校验并还原一项可排队任务。"""
    if payload is None:
        return None

    data = _mapping(payload, "agent submission")
    kind = _submission_kind(data.get("kind"))

    return AgentSubmission(
        submission_id=_required_text(
            data.get("submission_id"),
            "submission id",
        ),
        message=_required_text(data.get("message"), "submission message"),
        kind=kind,
        created_at_ms=_positive_int(
            data.get("created_at_ms"),
            "submission timestamp",
        ),
        parent_turn_id=str(data.get("parent_turn_id") or ""),
    )


def _mailbox_from_payload(payload: typing.Any) -> AgentMailboxSnapshot:
    """校验并还原邮箱事件与消费位置。"""
    data     = _mapping(payload, "agent mailbox")
    events   = data.get("events")
    consumed = data.get("consumed")

    if not isinstance(events, list):
        raise TypeError("agent mailbox events must be a list")
    if not isinstance(consumed, list):
        raise TypeError("agent mailbox consumed cursors must be a list")
    return AgentMailboxSnapshot(
        sequence=_nonnegative_int(data.get("sequence"), "mailbox sequence"),
        events=tuple(_mailbox_event_from_payload(item) for item in events),
        consumed=tuple(_consumed_cursor_from_payload(item) for item in consumed),
    )


def _mailbox_event_from_payload(payload: typing.Any) -> AgentMailboxEvent:
    """校验并还原一项邮箱事件。"""
    data = _mapping(payload, "agent mailbox event")
    return AgentMailboxEvent(
        event_id=_required_text(data.get("event_id"), "mailbox event id"),
        sequence=_positive_int(data.get("sequence"), "mailbox event sequence"),
        kind=_mailbox_event_kind(data.get("kind")),
        created_at_ms=_positive_int(
            data.get("created_at_ms"),
            "mailbox event timestamp",
        ),
        source_agent_id=_required_text(
            data.get("source_agent_id"),
            "mailbox event source id",
        ),
        source_task_path=_required_text(
            data.get("source_task_path"),
            "mailbox event source path",
        ),
        recipient_agent_id=str(data.get("recipient_agent_id") or ""),
        recipient_task_path=str(data.get("recipient_task_path") or ""),
        message=str(data.get("message") or ""),
        status=str(data.get("status") or ""),
        submission_id=str(data.get("submission_id") or ""),
        queued_count=_nonnegative_int(
            data.get("queued_count"),
            "mailbox queued count",
        ),
        detail=str(data.get("detail") or ""),
    )


def _consumed_cursor_from_payload(
    payload: typing.Any,
) -> tuple[str, tuple[str, ...]]:
    """校验并还原一个读取主体的消费位置。"""
    data = _mapping(payload, "agent mailbox consumed cursor")
    event_ids = data.get("event_ids")
    if not isinstance(event_ids, list) or any(
        not isinstance(event_id, str)
        for event_id in event_ids
    ):
        raise TypeError("mailbox consumed event ids must be a list of strings")
    return (
        _required_text(data.get("reader_agent_id"), "mailbox reader id"),
        tuple(event_ids),
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


def _require_positive_integer(value: typing.Any, label: str) -> None:
    """校验存储边界使用的正整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")


def _normalize_now_ms(value: int | None) -> int:
    """返回用于存储清理的正整数毫秒时间。"""
    if value is None:
        return time.time_ns() // 1_000_000
    _require_positive_integer(value, "agent graph current timestamp")
    return value


if __name__ == "__main__":
    pass
