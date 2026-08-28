# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from agent.ports import (
    TurnExecutor,
    TurnExecutorResult
)
from agent.protocol import (
    RunEvent,
    SubmitTurnCommand
)
from .run_actor import RunActor

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


@dataclass(frozen=True, slots=True)
class RunExecution(typing.Generic[ResultValue]):
    """返回一次命令的原始结果和完整本地事件投影。"""

    command_id: str
    run_id: str
    value: ResultValue
    events: tuple[RunEvent, ...]


@dataclass(frozen=True, slots=True)
class _Submission(typing.Generic[ResultValue]):
    """保存 SessionLoop 队列中的命令和共享完成信号。"""

    command: SubmitTurnCommand
    future: asyncio.Future[RunExecution[ResultValue]]


class SessionLoop(typing.Generic[ResultValue]):
    """管理一个 Session 的提交队列、RunActor 和事件队列生命周期。"""

    def __init__(
        self,
        session_id: str,
        executor: TurnExecutor[ResultValue],
    ) -> None:
        """绑定一个本地 Session 和无状态主动 Turn 执行端口。"""
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")

        self.session_id = normalized_session_id
        self._executor = executor
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
        self._closing = False
        self._closed = False

    @property
    def closed(self) -> bool:
        """返回 SessionLoop 是否已经完成关闭。"""
        return self._closed

    async def start(self) -> None:
        """幂等启动唯一命令消费任务。"""
        if self._closed or self._closing:
            raise RuntimeError("session loop is closing")
        if self._worker is None:
            self._worker = asyncio.create_task(
                self._run(),
                name=f"agent session {self.session_id}",
            )

    async def execute(
        self,
        command: SubmitTurnCommand,
    ) -> RunExecution[ResultValue]:
        """提交命令，并让相同幂等身份共享同一执行结果。"""
        if command.session_id != self.session_id:
            raise ValueError("command belongs to another session")
        if self._closing or self._closed:
            raise RuntimeError("session loop is closing")
        await self.start()

        future = self._resolve_existing(command)
        if future is None:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._register(command, future)
            await self._submissions.put(_Submission(command, future))

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

            command = submission.command
            future = submission.future
            actor = RunActor(command, self._executor, self._publish)

            try:
                value = await actor.run()
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
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

    def _publish(self, event: RunEvent) -> None:
        """同步记录事件后放入 Session 对外事件队列。"""
        self._events_by_run.setdefault(event.run_id, []).append(event)
        self._events.put_nowait(event)


if __name__ == '__main__':
    pass
