# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from dataclasses import dataclass

from agent.ports.desktop import (
    ActivateDesktopWindow,
    DesktopCommand,
    DesktopError,
    DesktopInput,
    DesktopResult,
    DesktopScope,
    DesktopSessionFactory,
    DesktopSessionPort,
    ListDesktopWindows,
    ObserveDesktopWindow,
    ValidateDesktopObservation,
)


@dataclass(slots=True)
class _DesktopEntry:
    """保存单个 Harness 会话的原生资源与关闭进度。"""

    scope: DesktopScope | None = None
    session: DesktopSessionPort | None = None
    active: asyncio.Task[DesktopResult] | None = None
    closing: asyncio.Task[None] | None = None
    closed: bool = False


class DesktopRuntime:
    """持有进程内各会话的桌面资源，用同一 FIFO 锁串行执行桌面操作。

    组合根创建一个实例并注入原生工厂。根会话、子代理和后台会话按 sid 隔离引用；
    原生会话另持有操作级跨进程输入锁，不在模型思考或审批等待时占用桌面。
    """

    def __init__(self, factory: DesktopSessionFactory) -> None:
        """绑定工厂但不探测平台、不启动原生进程。"""
        self._factory = factory
        self._entries: dict[str, _DesktopEntry] = {}
        self._operation_lock = asyncio.Lock()
        self._closed = False
        self._closing: asyncio.Task[None] | None = None

    async def execute(self, scope: DesktopScope, command: DesktopCommand) -> DesktopResult:
        """在冻结范围内执行一次桌面操作，失败和取消均不重放。"""
        if not scope.session_id.strip() or not scope.cwd.strip():
            raise ValueError("Desktop session and workspace are required")
        if self._closed:
            raise DesktopError("unavailable", "Desktop runtime is closed")
        entry = self._entries.get(scope.session_id)
        if entry is None:
            if len(self._entries) >= 128:
                raise DesktopError("session_limit", "Too many desktop sessions")
            entry = _DesktopEntry()
            self._entries[scope.session_id] = entry
        async with self._operation_lock:
            if self._closed or entry.closed:
                raise DesktopError("unavailable", "Desktop session was closed while queued")
            active = asyncio.create_task(self._execute(entry, scope, command))
            entry.active = active
            try:
                return await active
            except DesktopError as error:
                if error.code in {"timeout", "protocol_error", "transport_closed", "unavailable"}:
                    await self.close_session(scope.session_id)
                raise
            except BaseException:
                await self.close_session(scope.session_id)
                raise
            finally:
                entry.active = None

    async def _execute(self, entry: _DesktopEntry, scope: DesktopScope, command: DesktopCommand) -> DesktopResult:
        """在唯一执行锁内替换变更的信封并调用原生端口。"""
        if entry.session is not None and entry.scope != scope:
            await entry.session.close()
            entry.session = None
        if entry.session is None:
            entry.session = await self._factory()
            entry.scope = scope
        if isinstance(command, ListDesktopWindows):
            return await entry.session.list_windows()
        if isinstance(command, ObserveDesktopWindow):
            return await entry.session.observe(command.window, screenshot=command.screenshot)
        if isinstance(command, ValidateDesktopObservation):
            await entry.session.validate_observation(command.window, command.observation)
            return None
        if isinstance(command, ActivateDesktopWindow):
            return await entry.session.activate(command.window)
        if isinstance(command, DesktopInput):
            return await entry.session.input(command)
        raise TypeError("Unsupported desktop command")

    async def reset_session(self, session_id: str) -> None:
        """通过同一关闭路径重置桌面引用，不重置 JS 内核。"""
        await self.close_session(session_id)

    async def close_session(self, session_id: str) -> None:
        """先禁止旧条目继续执行，再共享可重试的资源回收任务。"""
        entry = self._entries.get(session_id)
        if entry is None:
            return
        entry.closed = True
        closing = entry.closing
        if closing is None or (closing.done() and (closing.cancelled() or closing.exception() is not None)):
            closing = asyncio.create_task(self._close_entry(session_id, entry))
            entry.closing = closing
        await asyncio.shield(closing)

    async def _close_entry(self, session_id: str, entry: _DesktopEntry) -> None:
        """回收活动调用及原生进程，完成前保留所有权供失败重试。"""
        if entry.active is not None:
            entry.active.cancel()
            await asyncio.gather(entry.active, return_exceptions=True)
        if entry.session is not None:
            await entry.session.close()
        if self._entries.get(session_id) is entry:
            del self._entries[session_id]

    async def close(self) -> None:
        """拒绝新调用并关闭全部会话，等待方取消不会中断后台回收。"""
        self._closed = True
        closing = self._closing
        if closing is None or (closing.done() and (closing.cancelled() or closing.exception() is not None)):
            closing = asyncio.create_task(self._close_all())
            self._closing = closing
        await asyncio.shield(closing)

    async def _close_all(self) -> None:
        """尝试收束每个会话并保留所有关闭失败。"""
        outcomes = await asyncio.gather(*(self.close_session(key) for key in tuple(self._entries)), return_exceptions=True)
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        if failures:
            raise BaseExceptionGroup("Desktop cleanup failed", failures)


if __name__ == '__main__':
    pass
