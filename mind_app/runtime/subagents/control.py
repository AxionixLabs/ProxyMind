# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from collections import deque
from dataclasses import dataclass
from mind_nova.identifiers import short_uid
from mind_app.runtime.execution import AgentContext
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
    "closed",
]
AgentResumeStatus = typing.Literal[
    "completed",
    "failed",
    "interrupted",
]

FINAL_AGENT_STATUSES = frozenset({
    "completed",
    "failed",
    "interrupted",
    "closed",
})


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

    def __init__(
        self,
        root: AgentContext,
        executor: AgentTurnExecutor,
        *,
        max_open_agents: int = 4,
        max_depth: int = 1
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

        self._root            = root
        self._executor        = executor
        self._max_open_agents = max_open_agents
        self._max_depth       = max_depth

        self._condition = asyncio.Condition()

        self._records: dict[str, _AgentRecord] = {}

        self._shutdown: bool = False

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
            if any(
                record.context.task_path == thread.agent.task_path
                for record in self._records.values()
            ):
                raise AgentStateError(
                    f"task path already exists: {thread.agent.task_path}"
                )

            record = _AgentRecord(thread=thread)
            self._records[thread.agent.agent_id] = record
            self._start(record, submission)
            self._condition.notify_all()
            return self._snapshot(record)

    async def submit(
        self,
        agent_id: str,
        submission: AgentSubmission,
        *,
        interrupt: bool = False
    ) -> str:
        """向已开放的执行主体提交或排队新一轮任务。"""
        if not isinstance(submission, AgentSubmission):
            raise TypeError("agent submission is required")
        if submission.kind != "followup":
            raise AgentStateError("submit requires a followup submission")

        async with self._condition:
            self._require_active()
            record = self._require_record(agent_id)
            if record.status == "closed":
                raise AgentStateError(f"agent is closed: {record.context.agent_id}")

            if record.status in {"pending", "running"}:
                if interrupt:
                    record.queue.appendleft(submission)
                    if record.task is not None:
                        _request_cancel(record.task)
                else:
                    record.queue.append(submission)
            else:
                self._start(record, submission)

            self._condition.notify_all()
            return submission.submission_id

    async def resume(self, agent_id: str) -> AgentSnapshot:
        """重新开放已经关闭的执行主体。"""
        async with self._condition:
            self._require_active()
            record = self._require_record(agent_id)
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
            self._condition.notify_all()
            return self._snapshot(record)

    async def get(self, agent_id: str) -> AgentSnapshot:
        """返回指定执行主体的当前快照。"""
        async with self._condition:
            return self._snapshot(self._require_record(agent_id))

    async def snapshots(self) -> tuple[AgentSnapshot, ...]:
        """按创建顺序返回全部执行主体快照。"""
        async with self._condition:
            return tuple(
                self._snapshot(record)
                for record in self._records.values()
            )

    async def count_open(self) -> int:
        """返回尚未关闭的执行主体数量。"""
        async with self._condition:
            return self._open_count()

    async def wait(
        self,
        targets: typing.Iterable[str],
        *,
        timeout_sec: float | None = None
    ) -> AgentWaitResult:
        """等待任一目标进入终态并返回当前终态快照。"""
        target_ids = _normalize_targets(targets)
        if (
            timeout_sec is not None
            and (
                isinstance(timeout_sec, bool)
                or not isinstance(timeout_sec, (int, float))
                or timeout_sec < 0
            )
        ):
            raise ValueError("agent wait timeout must be non-negative")

        loop = asyncio.get_running_loop()

        deadline = (
            loop.time() + float(timeout_sec)
            if timeout_sec is not None
            else None
        )

        async with self._condition:
            for agent_id in target_ids:
                self._require_record(agent_id)

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

    async def interrupt(self, agent_id: str) -> AgentSnapshot:
        """中断指定执行主体的当前轮次任务。"""
        task: asyncio.Task[None] | None = None

        submission_id: str = ""

        async with self._condition:
            record = self._require_record(agent_id)
            if record.status in {"pending", "running"}:
                task = record.task
                submission_id = record.submission_id
                if task is not None:
                    _request_cancel(task)

        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
            async with self._condition:
                record = self._records.get(agent_id)
                if (
                    record is not None
                    and record.submission_id == submission_id
                    and record.status in {"pending", "running"}
                ):
                    record.status = "interrupted"
                    record.result = None
                    record.error = ""
                if record is not None and record.task is task:
                    record.task = None
                self._condition.notify_all()

        return await self.get(agent_id)

    async def close(self, agent_id: str) -> AgentSnapshot:
        """关闭指定执行主体及其全部后代并返回关闭前快照。"""
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
        await self._close_records(None)
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

            if record.queue:
                self._start(record, record.queue.popleft())

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
        root_agent_id: str | None
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
                if agent_record.status != "closed":
                    agent_record.status_before_close = _status_for_resume(
                        agent_record.status
                    )

                agent_record.status = "closed"
                agent_record.queue.clear()
                agent_record.result = None
                agent_record.error  = ""

                if agent_record.task is not None:
                    _request_cancel(agent_record.task)
                    tasks.append((
                        agent_id,
                        agent_record.task,
                    ))
            self._condition.notify_all()

        if tasks:
            await asyncio.gather(
                *(task for _, task in tasks),
                return_exceptions=True,
            )
            for agent_id, task in tasks:
                await self._clear_task(agent_id, task)

        return previous

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

    def _require_record(self, agent_id: str) -> _AgentRecord:
        """返回规范标识对应的内部记录。"""
        normalized = str(agent_id or "").strip()

        record = self._records.get(normalized)
        if record is None:
            raise AgentNotFoundError(f"agent not found: {normalized or '<empty>'}")

        return record

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


def _status_for_resume(status: AgentStatus) -> AgentResumeStatus:
    """返回关闭后重新开放时应恢复的状态。"""
    if status == "completed":
        return "completed"
    if status == "failed":
        return "failed"
    if status in {"pending", "running", "interrupted"}:
        return "interrupted"

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
