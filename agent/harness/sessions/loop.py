# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from collections.abc import (
    Awaitable,
    Callable,
)
from dataclasses import dataclass

from agent.domain import (
    RecoveryAction,
    RunState,
)
from agent.ports import (
    RunExecution,
    RunPersistence,
    RunPersistenceConflict,
    RunRecoveryRequired,
    RunSnapshot,
    TurnExecutor,
    TurnExecutorResult
)
from agent.protocol import (
    RunEvent,
    SubmitTurnCommand
)
from observability import observe
from ..execution.actor import RunActor

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


@dataclass(frozen=True, slots=True)
class _Submission(typing.Generic[ResultValue]):
    """保存 SessionLoop 队列中的命令和共享完成信号。"""

    actor: RunActor[ResultValue]
    future: asyncio.Future[RunExecution[ResultValue]]


class SessionLoop(typing.Generic[ResultValue]):
    """管理一个 Session 的提交队列、RunActor 和事件队列生命周期。"""

    def __init__(
        self,
        session_id: str,
        executor: TurnExecutor[ResultValue] | None = None,
        persistence: RunPersistence | None = None,
    ) -> None:
        """绑定一个本地 Session 和可选的默认 Turn 执行端口。"""
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")

        self.session_id = normalized_session_id
        self._executor = executor
        self._persistence = persistence
        self._submissions: asyncio.Queue[_Submission[ResultValue] | None] = (
            asyncio.Queue()
        )
        self._events: asyncio.Queue[RunEvent] = asyncio.Queue()
        self._events_by_run: dict[str, list[RunEvent]] = {}
        self._futures_by_command: dict[
            str,
            asyncio.Future[RunExecution[ResultValue]],
        ] = {}
        self._futures_by_idempotency: dict[
            str,
            asyncio.Future[RunExecution[ResultValue]],
        ] = {}
        self._fingerprints: dict[str, str] = {}
        self._run_commands: dict[str, str] = {}
        self._worker: asyncio.Task[None] | None = None
        self._submit_lock: asyncio.Lock = asyncio.Lock()
        self._initialize_lock: asyncio.Lock = asyncio.Lock()
        self._recoveries: tuple[RunSnapshot, ...] = ()
        self._recovery_refresh_failed: bool = False
        self._initialized: bool = False
        self._closing: bool = False
        self._closed: bool = False

    @property
    def closed(self) -> bool:
        """返回 SessionLoop 是否已经完成关闭。"""
        return self._closed

    async def start(self) -> None:
        """幂等启动唯一命令消费任务。"""
        if self._closed or self._closing:
            raise RuntimeError("session loop is closing")
        await self._initialize()
        if self._worker is None:
            self._worker = asyncio.create_task(
                self._run(),
                name=f"agent session {self.session_id}",
            )

    async def execute(
        self,
        command: SubmitTurnCommand,
        executor: TurnExecutor[ResultValue] | None = None,
    ) -> RunExecution[ResultValue]:
        """提交命令，并让相同幂等身份共享同一执行结果。"""
        if command.session_id != self.session_id:
            raise ValueError("command belongs to another session")
        if self._closing or self._closed:
            raise RuntimeError("session loop is closing")
        await self.start()

        async with self._submit_lock:
            future = self._resolve_existing(command)
            if future is None:
                resolved_executor = executor or self._executor
                if resolved_executor is None:
                    raise ValueError("turn executor is required")
                actor = await self._prepare_actor(command, resolved_executor)
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._register(command, future)
                await self._submissions.put(_Submission(actor, future))

        return await asyncio.shield(future)

    async def next_event(self) -> RunEvent:
        """等待事件队列中的下一项状态事实。"""
        return await self._events.get()

    def events_for(self, run_id: str) -> tuple[RunEvent, ...]:
        """返回指定 Run 当前已经发布的事件快照。"""
        return tuple(self._events_by_run.get(run_id, ()))

    def drain_events(self) -> tuple[RunEvent, ...]:
        """按发布顺序取出当前事件队列中的全部事件。"""
        events: list[RunEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except asyncio.QueueEmpty:
                return tuple(events)

    async def close(self, *, cancel_running: bool = False) -> None:
        """停止接收命令并等待队列收束，或显式取消活动 Run。"""
        if self._closed:
            return None
        self._closing = True
        worker = self._worker

        if worker is not None and not worker.done():
            if cancel_running:
                worker.cancel()
            else:
                await self._submissions.put(None)
            await asyncio.gather(worker, return_exceptions=True)

        if cancel_running:
            while True:
                try:
                    submission = self._submissions.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if submission is None:
                    continue
                await submission.actor.cancel_queued()

        for future in set(self._futures_by_command.values()):
            if not future.done():
                future.cancel()

        self._worker = None
        self._closed = True

    def _resolve_existing(
        self,
        command: SubmitTurnCommand,
    ) -> asyncio.Future[RunExecution[ResultValue]] | None:
        """按 command id 或幂等键解析已经登记的执行。"""
        future = self._futures_by_command.get(command.command_id)
        if future is None:
            future = self._futures_by_idempotency.get(
                command.idempotency_key
            )
        if future is None:
            return None

        expected = self._fingerprints.get(command.idempotency_key)
        if expected != command.fingerprint():
            raise ValueError("idempotency key was reused for another command")
        return future

    def _register(
        self,
        command: SubmitTurnCommand,
        future: asyncio.Future[RunExecution[ResultValue]],
    ) -> None:
        """登记命令、幂等身份和 Run 的唯一归属。"""
        existing_command = self._run_commands.get(command.run_id)
        if (
            existing_command is not None
            and existing_command != command.command_id
        ):
            raise ValueError("run_id already belongs to another command")

        self._futures_by_command[command.command_id] = future
        self._futures_by_idempotency[command.idempotency_key] = future
        self._fingerprints[command.idempotency_key] = command.fingerprint()
        self._run_commands[command.run_id] = command.command_id

    async def _run(self) -> None:
        """在唯一 worker 中依次执行队列里的 RunActor。"""
        while True:
            submission = await self._submissions.get()
            if submission is None:
                return None

            actor = submission.actor
            command = actor.command
            future = submission.future

            gate_error = self._execution_gate_error(command)
            if gate_error is not None:
                self._release_blocked_future(command, future)
                if not future.done():
                    future.set_exception(gate_error)
                continue

            try:
                value = await actor.run()
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except BaseException as error:
                try:
                    await self._refresh_recoveries()
                except BaseException as recovery_error:
                    error = recovery_error
                if not future.done():
                    future.set_exception(error)
            else:
                try:
                    await self._refresh_recoveries()
                except BaseException as error:
                    if not future.done():
                        future.set_exception(error)
                else:
                    if not future.done():
                        future.set_result(RunExecution(
                            command_id=command.command_id,
                            run_id=command.run_id,
                            value=value,
                            events=self.events_for(command.run_id),
                        ))

    async def _initialize(self) -> None:
        """首次启动时读取 Session 的未终结恢复快照。"""
        if self._initialized:
            return None
        async with self._initialize_lock:
            if self._initialized:
                return None
            if self._persistence is not None:
                await self._refresh_recoveries()
                if self._recoveries:
                    observe(
                        "run.recovery_required",
                        session_id=self.session_id,
                        recoveries=tuple(
                            {
                                "run_id": item.command.run_id,
                                "status": item.status.value,
                                "sequence": item.sequence,
                                "effect_id": item.effect_id,
                                "effect_status": item.effect_status,
                                "action": (
                                    item.recovery_action.value
                                    if item.recovery_action is not None
                                    else ""
                                ),
                                "updated_at": item.updated_at,
                            }
                            for item in self._recoveries
                        ),
                    )
            self._initialized = True

    async def _prepare_actor(
        self,
        command: SubmitTurnCommand,
        executor: TurnExecutor[ResultValue],
    ) -> RunActor[ResultValue]:
        """创建新 RunActor，或仅对同一 queued 命令执行安全恢复。"""
        persisted = (
            await self._persistence.find_run(command)
            if self._persistence is not None
            else None
        )
        if persisted is not None:
            if persisted.recovery_action is not RecoveryAction.REDISPATCH:
                raise RunPersistenceConflict("persisted run cannot be redispatched")
            gate_error = self._execution_gate_error(persisted.command)
            if gate_error is not None:
                raise gate_error
            persisted_command = persisted.command
            events = await self._persistence.load_events(persisted_command.run_id)
            self._events_by_run[persisted_command.run_id] = list(events)
            return RunActor(
                persisted_command,
                executor,
                self._event_sink_for(persisted_command),
                state=RunState(
                    session_id=persisted_command.session_id,
                    run_id=persisted_command.run_id,
                    status=persisted.status,
                    sequence=persisted.sequence,
                ),
            )
        gate_error = self._execution_gate_error(command)
        if gate_error is not None:
            raise gate_error

        actor = RunActor(command, executor, self._event_sink_for(command))
        await actor.enqueue()
        return actor

    def _recovery_blocks(self, command: SubmitTurnCommand) -> bool:
        """判断最新恢复快照是否禁止指定 queued Run 开始执行。"""
        blockers = tuple(
            item
            for item in self._recoveries
            if item.recovery_action is not RecoveryAction.REDISPATCH
        )
        queued = tuple(
            item
            for item in self._recoveries
            if item.recovery_action is RecoveryAction.REDISPATCH
        )
        return bool(
            blockers
            or (
                queued
                and queued[0].command.run_id != command.run_id
            )
        )

    def _execution_gate_error(
        self,
        command: SubmitTurnCommand,
    ) -> RunPersistenceConflict | RunRecoveryRequired | None:
        """返回阻止命令执行的恢复门禁错误。"""
        if self._recovery_refresh_failed:
            return RunPersistenceConflict(
                "session recovery snapshot is unavailable"
            )
        if self._recovery_blocks(command):
            return RunRecoveryRequired(self._recoveries)
        return None

    def _release_blocked_future(
        self,
        command: SubmitTurnCommand,
        future: asyncio.Future[RunExecution[ResultValue]],
    ) -> None:
        """释放未执行命令的易失去重引用，保留持久 queued 事实。"""
        if self._futures_by_command.get(command.command_id) is future:
            del self._futures_by_command[command.command_id]
        if self._futures_by_idempotency.get(command.idempotency_key) is future:
            del self._futures_by_idempotency[command.idempotency_key]

    async def _refresh_recoveries(self) -> None:
        """在 Run 收束后刷新阻止后续盲目派发的恢复门禁。"""
        if self._persistence is None:
            self._recovery_refresh_failed = False
            return None
        try:
            recoveries = await self._persistence.recover_session(
                self.session_id
            )
        except BaseException:
            self._recovery_refresh_failed = True
            raise
        self._recoveries = recoveries
        self._recovery_refresh_failed = False

    async def refresh_recoveries(self) -> tuple[RunSnapshot, ...]:
        """刷新并返回当前 Session 单写者使用的恢复门禁快照。"""
        await self._initialize()
        async with self._submit_lock:
            await self._refresh_recoveries()
            return self._recoveries

    def _event_sink_for(
        self,
        command: SubmitTurnCommand,
    ) -> Callable[[RunEvent], Awaitable[None]]:
        """把当前 Run 命令显式绑定到异步事件提交出口。"""

        async def publish(event: RunEvent) -> None:
            """持久提交一项事件后更新当前 Session 的易失投影。"""
            if self._persistence is not None:
                await self._persistence.append_event(command, event)
            self._events_by_run.setdefault(event.run_id, []).append(event)
            self._events.put_nowait(event)

        return publish


if __name__ == '__main__':
    pass
