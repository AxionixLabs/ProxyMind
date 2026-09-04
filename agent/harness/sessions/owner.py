# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

from agent.ports import (
    RunPersistence,
    RunSnapshot,
    TurnExecutor,
    TurnExecutorResult
)
from agent.protocol import SubmitTurnCommand
from .loop import (
    RunExecution,
    SessionLoop
)

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


class SessionRuntimeOwner(typing.Generic[ResultValue]):
    """持有长生命周期 SessionLoop，并统一收束取消与关闭。"""

    def __init__(self, persistence: RunPersistence | None = None) -> None:
        """创建尚未启动任何 SessionLoop 的运行时所有者。"""
        self._persistence = persistence
        self._sessions: dict[str, SessionLoop[ResultValue]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._closing: bool = False
        self._closed: bool = False

    @property
    def closed(self) -> bool:
        """返回所有者是否已经完成关闭。"""
        return self._closed

    async def execute(
        self,
        command: SubmitTurnCommand,
        executor: TurnExecutor[ResultValue],
    ) -> RunExecution[ResultValue]:
        """在命令所属 Session 的唯一队列中提交一次执行。"""
        session = await self._session_for(command.session_id)
        return await session.execute(command, executor)

    async def cancel_session(self, session_id: str) -> None:
        """取消并移除指定 Session，等待活动 Run 完成清理。"""
        session = await self._take_session(session_id)
        if session is not None:
            await session.close(cancel_running=True)

    async def close_session(self, session_id: str) -> None:
        """停止指定 Session 接收命令，并等待已提交队列自然收束。"""
        session = await self._take_session(session_id)
        if session is not None:
            await session.close()

    async def recover_session(
        self,
        session_id: str,
    ) -> tuple[RunSnapshot, ...]:
        """刷新并读取指定 Session 的未终结 Run，不触发外部能力重放。"""
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if self._persistence is None:
            return ()
        async with self._lock:
            session = self._sessions.get(normalized_session_id)
        if session is not None:
            return await session.refresh_recoveries()
        return await self._persistence.recover_session(normalized_session_id)

    async def close(self, *, cancel_running: bool = False) -> None:
        """停止全部 Session，并按调用方选择等待或取消活动 Run。"""
        async with self._lock:
            close_task = self._close_task
            if close_task is None and self._closed:
                return None
            if close_task is None:
                self._closing = True
                sessions = tuple(self._sessions.values())
                self._sessions.clear()
                close_task = asyncio.create_task(
                    self._close_sessions(
                        sessions,
                        cancel_running=cancel_running,
                    ),
                    name="agent session runtime close",
                )
                self._close_task = close_task

        await asyncio.shield(close_task)

    async def _close_sessions(
        self,
        sessions: tuple[SessionLoop[ResultValue], ...],
        *,
        cancel_running: bool,
    ) -> None:
        """关闭固定的 SessionLoop 快照并提交所有者终态。"""
        try:
            if sessions:
                await asyncio.gather(*(
                    session.close(cancel_running=cancel_running)
                    for session in sessions
                ))
        finally:
            async with self._lock:
                self._closed = True

    async def _session_for(
        self,
        session_id: str,
    ) -> SessionLoop[ResultValue]:
        """返回指定身份的现有 SessionLoop，或原子创建一个新实例。"""
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")

        async with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("session runtime is closing")
            session = self._sessions.get(normalized_session_id)
            if session is None:
                session = SessionLoop(
                    normalized_session_id,
                    persistence=self._persistence,
                )
                self._sessions[normalized_session_id] = session
            return session

    async def _take_session(
        self,
        session_id: str,
    ) -> SessionLoop[ResultValue] | None:
        """从所有者中原子移除并返回指定 SessionLoop。"""
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        async with self._lock:
            return self._sessions.pop(normalized_session_id, None)


if __name__ == '__main__':
    pass
