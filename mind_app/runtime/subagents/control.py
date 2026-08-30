# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from collections import deque
from dataclasses import dataclass
from observability import observe_exception
from protocol.schema.identifiers import short_uid
from mind_app.runtime.execution import AgentContext
from mind_app.runtime.subagents.mailbox import (
    AgentMailboxEvent,
    AgentMailboxSnapshot,
    AgentMailboxStore
)
from mind_app.runtime.subagents.thread import (
    AgentThreadContext,
    AgentTurnContext
)

AgentStatus = typing.Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "interrupted",
    "interrupted_by_restart",
    "closed",
]
AgentResumeStatus = typing.Literal[
    "completed",
    "failed",
    "interrupted",
    "interrupted_by_restart",
]

FINAL_AGENT_STATUSES = frozenset({
    "completed",
    "failed",
    "interrupted",
    "interrupted_by_restart",
    "closed",
})

RESTART_INTERRUPTION_ERROR = "agent execution interrupted by process restart"


class AgentControlError(RuntimeError):
    """表示本地执行主体控制操作失败。"""


class AgentNotFoundError(AgentControlError):
    """表示指定执行主体不存在。"""


class AgentLimitError(AgentControlError):
    """表示根会话树已经达到开放数量限制。"""


class AgentDepthError(AgentControlError):
    """表示子级深度超过根会话树限制。"""


class AgentStateError(AgentControlError):
    """表示执行主体当前状态不允许指定操作。"""


AgentSubmissionKind = typing.Literal["initial", "followup"]


@dataclass(frozen=True, slots=True)
class AgentSubmission:
    """保存可排队和持久化的执行主体任务。"""
    submission_id: str
    message: str
    kind: AgentSubmissionKind
    created_at_ms: int
    parent_turn_id: str = ""

    def __post_init__(self) -> None:
        """校验任务载荷中的稳定字段。"""
        submission_id = str(self.submission_id or "").strip()
        message = str(self.message or "").strip()
        parent_turn_id = str(self.parent_turn_id or "").strip()

        if not submission_id:
            raise ValueError("agent submission id is required")
        if not message:
            raise ValueError("agent submission message is required")
        if self.kind not in {"initial", "followup"}:
            raise ValueError("agent submission kind is invalid")
        if (
            isinstance(self.created_at_ms, bool)
            or not isinstance(self.created_at_ms, int)
            or self.created_at_ms <= 0
        ):
            raise ValueError("agent submission timestamp must be positive")

        object.__setattr__(self, "submission_id", submission_id)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "parent_turn_id", parent_turn_id)

    @classmethod
    def create(
        cls,
        message: str,
        *,
        kind: AgentSubmissionKind,
        parent_turn_id: str = ""
    ) -> "AgentSubmission":
        """创建带稳定标识和时间的任务载荷。"""
        if not isinstance(message, str):
            raise TypeError("agent submission message must be a string")
        return cls(
            submission_id=short_uid(12),
            message=message,
            kind=kind,
            created_at_ms=time.time_ns() // 1_000_000,
            parent_turn_id=parent_turn_id,
        )


AgentTurnExecutor = typing.Callable[
    [AgentTurnContext, AgentSubmission],
    typing.Awaitable[typing.Any],
]


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """保存执行主体当前状态的不可变快照。"""
    thread: AgentThreadContext
    status: AgentStatus
    submission: AgentSubmission | None = None
    submission_id: str = ""
    turn_count: int = 0
    queued_count: int = 0
    result: typing.Any = None
    error: str = ""

    @property
    def agent_id(self) -> str:
        """返回执行主体标识。"""
        return self.thread.agent.agent_id

    @property
    def context(self) -> AgentContext:
        """返回执行主体身份。"""
        return self.thread.agent


@dataclass(frozen=True, slots=True)
class AgentWaitResult:
    """保存等待操作得到的终态快照。"""
    snapshots: tuple[AgentSnapshot, ...] = ()
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class AgentMailboxWaitResult:
    """保存动态等待得到的事件和目标快照。"""
    events: tuple[AgentMailboxEvent, ...] = ()
    snapshots: tuple[AgentSnapshot, ...] = ()
    timed_out: bool = False


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


AgentGraphPublisher = typing.Callable[[AgentGraphCheckpoint], None]


class _AgentRecord:
    """保存根会话树内部的可变执行状态。"""

    __slots__ = (
        "thread",
        "status",
        "submission",
        "submission_id",
        "turn_count",
        "task",
        "queue",
        "result",
        "error",
        "status_before_close",
    )

    thread: AgentThreadContext
    status: AgentStatus
    submission: AgentSubmission | None
    submission_id: str
    turn_count: int
    task: asyncio.Task[None] | None
    queue: deque[AgentSubmission]
    result: typing.Any
    error: str
    status_before_close: AgentResumeStatus | None

    def __init__(self, thread: AgentThreadContext) -> None:
        self.thread              = thread
        self.status              = "pending"
        self.submission          = None
        self.submission_id       = ""
        self.turn_count          = 0
        self.task                = None
        self.queue               = deque()
        self.result              = None
        self.error               = ""
        self.status_before_close = None

    @property
    def context(self) -> AgentContext:
        """返回线程中的执行主体身份。"""
        return self.thread.agent


class AgentControl:
    """管理单个根会话树中的执行主体和轮次任务。"""

    SHUTDOWN_WAIT_TIMEOUT_SEC: typing.Final[float]       = 2.0
    SHUTDOWN_FORCE_WAIT_TIMEOUT_SEC: typing.Final[float] = 0.1

    def __init__(
        self,
        root: AgentContext,
        executor: AgentTurnExecutor,
        *,
        max_open_agents: int = 4,
        max_depth: int = 1,
        checkpoint_publisher: AgentGraphPublisher | None = None,
    ) -> None:
        if root.depth != 0:
            raise ValueError("agent control requires a root context")
        if not callable(executor):
            raise TypeError("agent turn executor must be callable")
        if (
            isinstance(max_open_agents, bool)
            or not isinstance(max_open_agents, int)
            or max_open_agents <= 0
        ):
            raise ValueError("max open agents must be a positive integer")
        if (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or max_depth < 0
        ):
            raise ValueError("max agent depth must be a non-negative integer")
        if checkpoint_publisher is not None and not callable(checkpoint_publisher):
            raise TypeError("agent graph checkpoint publisher must be callable")

        self._root            = root
        self._executor        = executor
        self._max_open_agents = max_open_agents
        self._max_depth       = max_depth

        self._checkpoint_publisher = checkpoint_publisher

        self._condition = asyncio.Condition()

        self._records: dict[str, _AgentRecord] = {}

        self._records_by_path: dict[str, _AgentRecord] = {}

        self._mailbox = AgentMailboxStore()

        self._shutdown: bool = False
        self._revision: int  = 0

    @classmethod
    def restore(
        cls,
        checkpoint: AgentGraphCheckpoint,
        executor: AgentTurnExecutor,
        *,
        max_open_agents: int = 4,
        max_depth: int = 1,
        checkpoint_publisher: AgentGraphPublisher | None = None,
    ) -> "AgentControl":
        """从持久化快照重建不自动执行旧任务的控制树。"""
        if not isinstance(checkpoint, AgentGraphCheckpoint):
            raise TypeError("agent graph checkpoint is required")

        control = cls(
            AgentContext.root(checkpoint.root_session_id),
            executor,
            max_open_agents=max_open_agents,
            max_depth=max_depth,
            checkpoint_publisher=checkpoint_publisher,
        )
        control._revision = checkpoint.revision
        control._mailbox = AgentMailboxStore.from_snapshot(checkpoint.mailbox)

        for saved in checkpoint.records:
            control._require_thread_parent(saved.thread)
            record = _AgentRecord(saved.thread)
            record.status = saved.status
            record.submission = saved.submission
            record.submission_id = (
                saved.submission.submission_id
                if saved.submission is not None
                else ""
            )
            record.turn_count = saved.turn_count
            record.queue.extend(saved.queue)
            record.error = saved.error
            record.status_before_close = saved.status_before_close

            if saved.status in {"pending", "running"}:
                record.status = "interrupted_by_restart"
                record.error = RESTART_INTERRUPTION_ERROR
                control._mailbox.publish(
                    "status",
                    record.context,
                    status="interrupted_by_restart",
                    submission_id=record.submission_id,
                    queued_count=len(record.queue),
                    detail=record.error,
                )

            control._records[record.context.agent_id] = record
            control._records_by_path[record.context.task_path] = record

        control._publish_checkpoint()
        return control

    @property
    def root(self) -> AgentContext:
        """返回根执行主体身份。"""
        return self._root

    @property
    def max_open_agents(self) -> int:
        """返回根会话树允许的开放执行主体数量。"""
        return self._max_open_agents

    @property
    def max_depth(self) -> int:
        """返回允许的最大子级深度。"""
        return self._max_depth

    async def spawn(
        self,
        thread: AgentThreadContext,
        submission: AgentSubmission
    ) -> AgentSnapshot:
        """分配子执行主体并提交首轮任务。"""
        if not isinstance(submission, AgentSubmission):
            raise TypeError("agent submission is required")
        if submission.kind != "initial":
            raise AgentStateError("spawn requires an initial submission")

        async with self._condition:
            self._require_active()
            self._require_thread_parent(thread)

            if thread.agent.depth > self._max_depth:
                raise AgentDepthError(
                    f"agent depth {thread.agent.depth} "
                    f"exceeds limit {self._max_depth}"
                )
            if self._open_count() >= self._max_open_agents:
                raise AgentLimitError(
                    f"open agent limit reached: {self._max_open_agents}"
                )

            if thread.agent.agent_id in self._records:
                raise AgentStateError(
                    f"agent id already exists: {thread.agent.agent_id}"
                )
            if thread.agent.task_path in self._records_by_path:
                raise AgentStateError(
                    f"task path already exists: {thread.agent.task_path}"
                )

            record = _AgentRecord(thread=thread)
            self._records[thread.agent.agent_id] = record
            self._records_by_path[thread.agent.task_path] = record
            self._start(record, submission)
            self._publish_checkpoint()
            self._condition.notify_all()

            return self._snapshot(record)

    async def followup(
        self,
        target: str,
        submission: AgentSubmission,
        *,
        caller: AgentContext | None = None,
    ) -> str:
        """向已开放的执行主体提交或排队新一轮任务。"""
        if not isinstance(submission, AgentSubmission):
            raise TypeError("agent submission is required")
        if submission.kind != "followup":
            raise AgentStateError("followup requires a followup submission")

        async with self._condition:
            self._require_active()
            record = self._require_record(target, caller=caller)
            if record.status == "closed":
                raise AgentStateError(f"agent is closed: {record.context.agent_id}")

            if record.status in {"pending", "running"}:
                record.queue.append(submission)
            elif record.queue:
                record.queue.append(submission)
                self._start(record, record.queue.popleft())
            else:
                self._start(record, submission)

            self._mailbox.publish(
                "queue",
                record.context,
                submission_id=submission.submission_id,
                queued_count=len(record.queue),
            )
            self._publish_checkpoint()
            self._condition.notify_all()
            return submission.submission_id

    async def send_message(
        self,
        target: str,
        message: str,
        *,
        caller: AgentContext | None = None,
        claim_owner: str = "",
    ) -> AgentMailboxEvent:
        """向目标邮箱投递消息，并可为活动投递临时锁定。"""
        async with self._condition:
            self._require_active()
            source = self._require_caller(caller)
            recipient = self._require_target_context(target, caller=source)
            if source.agent_id == recipient.agent_id:
                raise AgentStateError("an agent cannot message itself")

            record = self._records.get(recipient.agent_id)
            if record is not None and record.status == "closed":
                raise AgentStateError(
                    f"agent is closed: {recipient.agent_id}"
                )

            event = self._mailbox.publish(
                "message",
                source,
                recipient=recipient,
                message=message,
            )
            if claim_owner and not self._mailbox.claim_message(
                recipient.agent_id,
                event.event_id,
                claim_owner,
            ):
                raise AgentStateError("mailbox message could not be claimed")
            self._publish_checkpoint()
            self._condition.notify_all()
            return event

    async def claim_messages(
        self,
        target: str,
        owner_id: str,
    ) -> tuple[AgentMailboxEvent, ...]:
        """为一次轮次临时锁定目标执行主体的未读消息。"""
        async with self._condition:
            record = self._require_record(target)
            return self._mailbox.claim_messages(
                record.context.agent_id,
                owner_id,
            )

    async def acknowledge_messages(
        self,
        target: str,
        owner_id: str,
        events: typing.Collection[AgentMailboxEvent],
    ) -> tuple[str, ...]:
        """确认一次投递已完成的邮箱消息。"""
        async with self._condition:
            record = self._require_record(target)
            acknowledged = self._mailbox.acknowledge_claim(
                record.context.agent_id,
                owner_id,
                tuple(event.event_id for event in events),
            )
            if acknowledged:
                self._publish_checkpoint()
            return acknowledged

    async def release_messages(
        self,
        target: str,
        owner_id: str,
        events: typing.Collection[AgentMailboxEvent],
    ) -> tuple[str, ...]:
        """释放一次未完成投递临时锁定的邮箱消息。"""
        async with self._condition:
            record = self._require_record(target)
            released = self._mailbox.release_claim(
                record.context.agent_id,
                owner_id,
                tuple(event.event_id for event in events),
            )
            if released:
                self._condition.notify_all()
            return released

    async def resume(
        self,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentSnapshot:
        """重新开放已经关闭的执行主体。"""
        async with self._condition:
            self._require_active()
            record = self._require_record(target, caller=caller)
            if record.status != "closed":
                return self._snapshot(record)
            if record.task is not None:
                raise AgentStateError(
                    f"agent is still closing: {record.context.agent_id}"
                )
            if self._open_count() >= self._max_open_agents:
                raise AgentLimitError(
                    f"open agent limit reached: {self._max_open_agents}"
                )

            if record.status_before_close is None:
                record.status = "interrupted"
            else:
                record.status = record.status_before_close
            record.status_before_close = None
            self._publish_checkpoint()
            self._condition.notify_all()
            return self._snapshot(record)

    async def get(
        self,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentSnapshot:
        """返回指定执行主体的当前快照。"""
        async with self._condition:
            return self._snapshot(self._require_record(target, caller=caller))

    async def snapshots(self) -> tuple[AgentSnapshot, ...]:
        """按创建顺序返回全部执行主体快照。"""
        return await self.list_snapshots()

    async def list_snapshots(
        self,
        *,
        caller: AgentContext | None = None,
        path_prefix: str | None = None,
    ) -> tuple[AgentSnapshot, ...]:
        """按创建顺序返回指定任务路径下的执行主体快照。"""
        async with self._condition:
            caller_context = self._require_caller(caller)
            canonical_prefix = (
                caller_context.resolve_task_reference(path_prefix)
                if path_prefix is not None
                else None
            )
            return tuple(
                self._snapshot(record)
                for record in self._records.values()
                if canonical_prefix is None
                or canonical_prefix == self._root.task_path
                or record.context.task_path == canonical_prefix
                or record.context.task_path.startswith(f"{canonical_prefix}/")
            )

    async def count_open(self) -> int:
        """返回尚未关闭的执行主体数量。"""
        async with self._condition:
            return self._open_count()

    async def wait(
        self,
        targets: typing.Iterable[str],
        *,
        timeout_sec: float | None = None,
        caller: AgentContext | None = None,
    ) -> AgentWaitResult:
        """等待任一目标进入终态并返回当前终态快照。"""
        normalized_targets = _normalize_targets(targets)
        _validate_wait_timeout(timeout_sec)

        loop = asyncio.get_running_loop()

        deadline = (
            loop.time() + float(timeout_sec)
            if timeout_sec is not None
            else None
        )

        async with self._condition:
            target_ids = tuple(dict.fromkeys(
                self._require_record(target, caller=caller).context.agent_id
                for target in normalized_targets
            ))
            if caller is not None and caller.agent_id in target_ids:
                raise AgentStateError(
                    "an agent cannot wait for its own active turn"
                )

            while True:
                snapshots = tuple(
                    self._snapshot(self._records[agent_id])
                    for agent_id in target_ids
                    if self._records[agent_id].status in FINAL_AGENT_STATUSES
                )
                if snapshots:
                    return AgentWaitResult(snapshots=snapshots)

                remaining = (
                    max(0.0, deadline - loop.time())
                    if deadline is not None
                    else None
                )
                if remaining == 0.0:
                    return AgentWaitResult(timed_out=True)

                try:
                    if remaining is None:
                        await self._condition.wait()
                    else:
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=remaining,
                        )
                except asyncio.TimeoutError:
                    return AgentWaitResult(timed_out=True)

    async def wait_updates(
        self,
        targets: typing.Iterable[str],
        *,
        timeout_sec: float | None = None,
        caller: AgentContext | None = None,
    ) -> AgentMailboxWaitResult:
        """等待目标的邮箱、队列或终态更新。"""
        normalized_targets = _normalize_targets(targets)
        _validate_wait_timeout(timeout_sec)

        loop = asyncio.get_running_loop()
        deadline = (
            loop.time() + float(timeout_sec)
            if timeout_sec is not None
            else None
        )

        async with self._condition:
            reader = self._require_caller(caller)
            target_contexts = tuple(dict.fromkeys(
                self._require_target_context(target, caller=reader)
                for target in normalized_targets
            ))
            target_ids = tuple(
                context.agent_id
                for context in target_contexts
            )
            if reader.agent_id in target_ids:
                raise AgentStateError(
                    "an agent cannot wait for its own active turn"
                )

            while True:
                events = self._mailbox.take_updates(
                    reader.agent_id,
                    target_ids,
                )
                snapshots = tuple(
                    self._snapshot(self._records[agent_id])
                    for agent_id in target_ids
                    if agent_id in self._records
                )
                if events:
                    self._publish_checkpoint()
                    return AgentMailboxWaitResult(
                        events=events,
                        snapshots=snapshots,
                    )

                final_snapshots = tuple(
                    snapshot
                    for snapshot in snapshots
                    if snapshot.status in FINAL_AGENT_STATUSES
                )
                if final_snapshots:
                    return AgentMailboxWaitResult(
                        snapshots=final_snapshots,
                    )

                remaining = (
                    max(0.0, deadline - loop.time())
                    if deadline is not None
                    else None
                )
                if remaining == 0.0:
                    return AgentMailboxWaitResult(timed_out=True)

                try:
                    if remaining is None:
                        await self._condition.wait()
                    else:
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=remaining,
                        )
                except asyncio.TimeoutError:
                    return AgentMailboxWaitResult(timed_out=True)

    async def interrupt(
        self,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentSnapshot:
        """中断指定执行主体的当前轮次任务。"""
        task: asyncio.Task[None] | None = None

        submission_id: str = ""

        agent_id: str

        async with self._condition:
            record = self._require_record(target, caller=caller)
            agent_id = record.context.agent_id
            if caller is not None and agent_id == caller.agent_id:
                raise AgentStateError(
                    "an agent cannot interrupt its own active turn"
                )
            if record.status in {"pending", "running"}:
                task = record.task
                submission_id = record.submission_id
                if task is not None:
                    _request_cancel(task)

        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
            async with self._condition:

                record  = self._records.get(agent_id)
                changed = False

                if (
                    record is not None
                    and record.submission_id == submission_id
                    and record.status in {"pending", "running"}
                ):
                    record.status = "interrupted"
                    record.result = None
                    record.error  = ""

                    changed = True

                    self._mailbox.publish(
                        "status",
                        record.context,
                        status="interrupted",
                        submission_id=submission_id,
                    )
                if record is not None and record.task is task:
                    record.task = None
                if changed:
                    self._publish_checkpoint()
                self._condition.notify_all()

        return await self.get(agent_id)

    async def close(
        self,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentSnapshot:
        """关闭指定执行主体及其全部后代并返回关闭前快照。"""
        async with self._condition:
            record = self._require_record(target, caller=caller)
            agent_id = record.context.agent_id
            self._reject_closing_caller_tree(agent_id, caller)
        previous = await self._close_records(agent_id)
        return previous[agent_id]

    async def close_all(self) -> tuple[AgentSnapshot, ...]:
        """关闭根会话树中的全部子执行主体。"""
        await self._close_records(None)
        return await self.snapshots()

    async def shutdown(self) -> tuple[AgentSnapshot, ...]:
        """终止控制器并关闭全部子执行主体。"""
        async with self._condition:
            self._shutdown = True
            self._condition.notify_all()
        await self._close_records(
            None,
            wait_timeout_sec=self.SHUTDOWN_WAIT_TIMEOUT_SEC,
        )
        return await self.snapshots()

    def _start(
        self,
        record: _AgentRecord,
        submission: AgentSubmission,
    ) -> None:
        """在持锁状态下启动一次轮次任务。"""
        record.status        = "pending"
        record.submission    = submission
        record.submission_id = submission.submission_id

        record.turn_count += 1

        record.result = None
        record.error  = ""

        record.task = asyncio.create_task(
            self._run_turn(
                record.context.agent_id,
                submission,
            ),
            name=f"subagent {record.context.agent_id}",
        )

    async def _run_turn(
        self,
        agent_id: str,
        submission: AgentSubmission,
    ) -> None:
        """执行任务并提交与当前轮次匹配的终态。"""
        submission_id = submission.submission_id

        async with self._condition:
            record = self._records.get(agent_id)
            if (
                record is None
                or record.submission_id != submission_id
                or record.status != "pending"
            ):
                return None

            record.status = "running"

            turn_context = AgentTurnContext(
                thread=record.thread,
                submission_id=submission_id,
                turn_index=record.turn_count,
            )
            self._publish_checkpoint()
            self._condition.notify_all()

        try:
            result = await self._executor(turn_context, submission)
        except asyncio.CancelledError:
            await self._commit_turn(
                agent_id,
                submission_id,
                status="interrupted",
            )
            raise
        except Exception as error:
            await self._commit_turn(
                agent_id,
                submission_id,
                status="failed",
                error=_bounded_error(error),
            )
        else:
            await self._commit_turn(
                agent_id,
                submission_id,
                status="completed",
                result=result,
            )

    async def _commit_turn(
        self,
        agent_id: str,
        submission_id: str,
        *,
        status: AgentStatus,
        result: typing.Any = None,
        error: str = ""
    ) -> None:
        """提交轮次终态，并在提交阶段取消时保证状态收束。"""
        try:
            await self._finish(
                agent_id,
                submission_id,
                status=status,
                result=result,
                error=error,
            )
        except asyncio.CancelledError:
            await self._finish(
                agent_id,
                submission_id,
                status="interrupted",
            )
            raise

    async def _finish(
        self,
        agent_id: str,
        submission_id: str,
        *,
        status: AgentStatus,
        result: typing.Any = None,
        error: str = ""
    ) -> None:
        """提交一次轮次的最终状态。"""
        async with self._condition:
            record = self._records.get(agent_id)
            if record is None or record.submission_id != submission_id:
                return None
            if record.status == "closed":
                return None

            record.status = status
            record.result = result
            record.error  = error
            record.task   = None

            self._mailbox.publish(
                "status",
                record.context,
                status=status,
                submission_id=submission_id,
                queued_count=len(record.queue),
                detail=_result_detail(result, error),
            )

            if record.queue:
                self._start(record, record.queue.popleft())

            self._publish_checkpoint()
            self._condition.notify_all()

    async def _clear_task(
        self,
        agent_id: str,
        task: asyncio.Task[None]
    ) -> None:
        """清除仍指向指定已结束任务的引用。"""
        async with self._condition:
            record = self._records.get(agent_id)
            if record is not None and record.task is task:
                record.task = None
                self._condition.notify_all()

    async def _close_records(
        self,
        root_agent_id: str | None,
        *,
        wait_timeout_sec: float | None = None,
    ) -> dict[str, AgentSnapshot]:
        """关闭给定执行主体并等待活动任务退出。"""
        tasks: list[tuple[str, asyncio.Task[None]]] = []
        previous: dict[str, AgentSnapshot]          = {}

        async with self._condition:
            if root_agent_id is None:
                target_ids = tuple(self._records)
            else:
                self._require_record(root_agent_id)
                target_ids = self._subtree_ids(root_agent_id)

            records = sorted(
                (
                    self._records[agent_id]
                    for agent_id in target_ids
                    if agent_id in self._records
                ),
                key=lambda item: item.context.depth,
                reverse=True,
            )

            for agent_record in records:
                agent_id = agent_record.context.agent_id
                previous[agent_id] = self._snapshot(agent_record)

                was_closed = agent_record.status == "closed"
                if not was_closed:
                    agent_record.status_before_close = _status_for_resume(
                        agent_record.status
                    )

                agent_record.status = "closed"
                agent_record.queue.clear()
                agent_record.result = None
                agent_record.error  = ""

                if not was_closed:
                    self._mailbox.publish(
                        "status",
                        agent_record.context,
                        status="closed",
                        submission_id=agent_record.submission_id,
                    )

                if agent_record.task is not None:
                    _request_cancel(agent_record.task)
                    tasks.append((
                        agent_id,
                        agent_record.task,
                    ))
            if records:
                self._publish_checkpoint()
            self._condition.notify_all()

        if tasks:
            pending_tasks: set[asyncio.Task[None]] = set()
            task_values = tuple(task for _, task in tasks)

            if wait_timeout_sec is None:
                await asyncio.gather(*task_values, return_exceptions=True)
            else:
                done_tasks, pending_tasks = await asyncio.wait(
                    task_values,
                    timeout=max(0.0, float(wait_timeout_sec)),
                )
                if done_tasks:
                    await asyncio.gather(*done_tasks, return_exceptions=True)

            if pending_tasks:
                graceful_timeout_ids = tuple(
                    agent_id
                    for agent_id, task in tasks
                    if task in pending_tasks
                )
                observe_exception(
                    "subagent.shutdown.timeout",
                    TimeoutError("subagent shutdown deadline exceeded"),
                    level="WARNING",
                    timeout_sec=wait_timeout_sec,
                    agent_ids=list(graceful_timeout_ids),
                )

                for task in pending_tasks:
                    task.cancel()

                forced_done, pending_tasks = await asyncio.wait(
                    pending_tasks,
                    timeout=self.SHUTDOWN_FORCE_WAIT_TIMEOUT_SEC,
                )
                if forced_done:
                    await asyncio.gather(
                        *forced_done,
                        return_exceptions=True,
                    )

                if pending_tasks:
                    for task in pending_tasks:
                        task.add_done_callback(self._observe_detached_task)

            for agent_id, task in tasks:
                await self._clear_task(agent_id, task)

        return previous

    @staticmethod
    def _observe_detached_task(task: asyncio.Task[None]) -> None:
        """观察超过强制取消截止时间后才完成的任务异常。"""
        if task.cancelled():
            return None
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return None
        if error is not None:
            observe_exception(
                "subagent.shutdown.task_failed",
                error,
                level="WARNING",
                task_name=task.get_name(),
            )

    def _publish_checkpoint(self) -> None:
        """向非阻塞端口发布当前执行树快照。"""
        publisher = self._checkpoint_publisher
        if publisher is None:
            return None

        self._revision += 1
        checkpoint = AgentGraphCheckpoint(
            root_session_id=self._root.root_session_id,
            revision=self._revision,
            updated_at_ms=time.time_ns() // 1_000_000,
            records=tuple(
                AgentGraphRecord(
                    thread=record.thread,
                    status=record.status,
                    submission=record.submission,
                    queue=tuple(record.queue),
                    turn_count=record.turn_count,
                    error=record.error,
                    status_before_close=record.status_before_close,
                )
                for record in self._records.values()
            ),
            mailbox=self._mailbox.snapshot(),
        )
        try:
            publisher(checkpoint)
        except Exception as error:
            observe_exception(
                "subagent.graph.publish_failed",
                error,
                level="WARNING",
                root_session_id=self._root.root_session_id,
                revision=self._revision,
            )

    def _require_thread_parent(self, thread: AgentThreadContext) -> None:
        """验证线程身份及父级属于当前根会话树。"""
        context = thread.agent
        if context.root_session_id != self._root.root_session_id:
            raise AgentStateError("parent belongs to another root session")
        if context.parent_agent_id == self._root.agent_id:
            if context.depth != 1:
                raise AgentDepthError("root child depth must be one")
            return None

        record = self._records.get(context.parent_agent_id)
        if record is None:
            raise AgentNotFoundError(
                f"parent agent not found: {context.parent_agent_id}"
            )
        if context.depth != record.context.depth + 1:
            raise AgentDepthError("child depth does not match parent depth")
        if record.status == "closed":
            raise AgentStateError(
                f"parent agent is closed: {context.parent_agent_id}"
            )

    def _require_active(self) -> None:
        """确认控制器仍可接受新任务。"""
        if self._shutdown:
            raise AgentStateError("agent control is shut down")

    def _require_caller(self, caller: AgentContext | None) -> AgentContext:
        """返回属于当前控制树的调用主体身份。"""
        if caller is None:
            return self._root
        if caller.root_session_id != self._root.root_session_id:
            raise AgentStateError("caller belongs to another root session")
        if caller.depth == 0:
            if caller != self._root:
                raise AgentStateError("caller does not match the root agent")
            return caller

        record = self._records.get(caller.agent_id)
        if record is None or record.context != caller:
            raise AgentStateError("caller is not part of the agent tree")
        return caller

    def _require_record(
        self,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> _AgentRecord:
        """按标识或任务路径返回内部记录。"""
        context = self._require_target_context(target, caller=caller)
        record  = self._records.get(context.agent_id)

        if record is None:
            normalized = str(target or "").strip()
            raise AgentNotFoundError(f"agent not found: {normalized or '<empty>'}")

        return record

    def _require_target_context(
        self,
        target: str,
        *,
        caller: AgentContext | None = None,
    ) -> AgentContext:
        """按标识或任务路径返回目标身份。"""
        caller_context = self._require_caller(caller)

        normalized = str(target or "").strip()
        if normalized == self._root.agent_id:
            return self._root

        record = self._records.get(normalized)
        if record is not None:
            return record.context

        if normalized:
            task_path = caller_context.resolve_task_reference(normalized)
            if task_path == self._root.task_path:
                return self._root
            record = self._records_by_path.get(task_path)
            if record is not None:
                return record.context

        raise AgentNotFoundError(f"agent not found: {normalized or '<empty>'}")

    def _reject_closing_caller_tree(
        self,
        target_id: str,
        caller: AgentContext | None,
    ) -> None:
        """拒绝关闭调用主体自身或祖先。"""
        context = self._require_caller(caller)
        if context.depth == 0:
            return None

        current_id: str | None = context.agent_id
        while current_id and current_id != self._root.agent_id:
            if current_id == target_id:
                raise AgentStateError(
                    "an agent cannot close itself or an ancestor"
                )
            record = self._records.get(current_id)
            current_id = (
                record.context.parent_agent_id
                if record is not None
                else None
            )

    def _subtree_ids(self, agent_id: str) -> tuple[str, ...]:
        """返回指定执行主体及其全部后代标识。"""
        selected = {agent_id}
        changed  = True

        while changed:
            changed = False
            for record in self._records.values():
                parent_id = record.context.parent_agent_id
                if record.context.agent_id not in selected and parent_id in selected:
                    selected.add(record.context.agent_id)
                    changed = True

        return tuple(
            record.context.agent_id
            for record in self._records.values()
            if record.context.agent_id in selected
        )

    def _open_count(self) -> int:
        """返回持锁状态下的开放执行主体数量。"""
        return sum(
            record.status != "closed" or record.task is not None
            for record in self._records.values()
        )

    @staticmethod
    def _snapshot(record: _AgentRecord) -> AgentSnapshot:
        """从内部记录创建不可变状态快照。"""
        return AgentSnapshot(
            thread=record.thread,
            status=record.status,
            submission=record.submission,
            submission_id=record.submission_id,
            turn_count=record.turn_count,
            queued_count=len(record.queue),
            result=record.result,
            error=record.error,
        )


def _normalize_targets(targets: typing.Iterable[str]) -> tuple[str, ...]:
    """规范并去重等待目标。"""
    values = (targets,) if isinstance(targets, str) else tuple(targets)

    normalized = tuple(dict.fromkeys(
        str(value or "").strip()
        for value in values
    ))

    if not normalized or any(not value for value in normalized):
        raise ValueError("at least one non-empty agent target is required")
    return normalized


def _validate_wait_timeout(timeout_sec: float | None) -> None:
    """校验等待超时值。"""
    if (
        timeout_sec is not None
        and (
            isinstance(timeout_sec, bool)
            or not isinstance(timeout_sec, (int, float))
            or timeout_sec < 0
        )
    ):
        raise ValueError("agent wait timeout must be non-negative")


def _result_detail(result: typing.Any, error: str) -> str:
    """返回适合邮箱事件的有界结果摘要。"""
    value = error or getattr(result, "assistant_text", "")
    text = str(value or "").strip()
    return text if len(text) <= 2000 else f"{text[:2000]}..."


def _status_for_resume(status: AgentStatus) -> AgentResumeStatus:
    """返回关闭后重新开放时应恢复的状态。"""
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    if status in {"pending", "running", "interrupted"}:
        return "interrupted"
    if status == "interrupted_by_restart":
        return "interrupted_by_restart"

    raise AgentStateError("closed agent has no resumable status")


def _bounded_error(error: Exception, limit: int = 2000) -> str:
    """返回包含异常类型的有界错误摘要。"""
    detail = str(error).strip()
    text   = f"{type(error).__name__}: {detail}" if detail else type(error).__name__
    return text if len(text) <= limit else f"{text[:limit]}..."


def _request_cancel(task: asyncio.Task[None]) -> None:
    """仅在任务尚未处理取消请求时发起取消。"""
    if not task.done() and task.cancelling() == 0:
        task.cancel()


if __name__ == '__main__':
    pass
