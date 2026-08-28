# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from engine.errors import AppError
from engine.manage import ServerManage
from engine.observability import (
    observe,
    observe_exception,
)
from engine.ports import terminate_port_process
from .keepalive import run_keepalive

if typing.TYPE_CHECKING:
    from .service_runtime import ServiceRuntimeContext

ServiceStartupOperation = typing.Callable[[], typing.Awaitable[bool]]


class ServiceRuntimeOwner(object):
    """持有本地服务管理器，并管理启动、保活与最终释放。"""

    def __init__(self) -> None:
        """初始化未绑定的服务运行时状态。"""
        self._manager: ServerManage | None = None
        self._context: ServiceRuntimeContext | None = None
        self._startup_task: asyncio.Task[bool] | None = None
        self._startup_lock = asyncio.Lock()
        self._keepalive_stop: asyncio.Event | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._terminate_on_close = False

    @property
    def manager(self) -> ServerManage | None:
        """返回已绑定的本地服务管理器。"""
        return self._manager

    def bind(
        self,
        manager: ServerManage,
        context: "ServiceRuntimeContext",
    ) -> None:
        """绑定本地服务管理器和对应准备上下文。"""
        self._manager = manager
        self._context = context

    def require_context(self) -> "ServiceRuntimeContext":
        """返回已绑定的准备上下文，未绑定时报错。"""
        context = self._context
        if context is None:
            raise AppError("Service runtime context is not bound")
        return context

    def request_termination_on_close(self) -> None:
        """标记应用退出时同时终止本地服务进程。"""
        self._terminate_on_close = True

    async def run_startup(self, operation: ServiceStartupOperation) -> bool:
        """复用正在执行的本地服务准备任务。"""
        async with self._startup_lock:
            task = self._startup_task
            if task is None:
                task = asyncio.create_task(
                    operation(),
                    name="service runtime startup",
                )
                self._startup_task = task

        try:
            return bool(await asyncio.shield(task))
        finally:
            if task.done():
                async with self._startup_lock:
                    if self._startup_task is task:
                        self._startup_task = None

    async def cancel_startup(self) -> None:
        """取消并回收尚未完成的本地服务准备任务。"""
        async with self._startup_lock:
            task = self._startup_task
            self._startup_task = None

        if task is None:
            return None
        if not task.done():
            task.cancel()

        await asyncio.gather(task, return_exceptions=True)

    def start_keepalive(self) -> None:
        """启动当前本地服务的后台保活任务。"""
        task = self._keepalive_task
        if task is not None and not task.done():
            return None

        manager = self._manager
        if manager is None:
            raise AppError("Server manager is not bound")

        stop_event = asyncio.Event()
        self._keepalive_stop = stop_event
        self._keepalive_task = asyncio.create_task(
            run_keepalive(stop_event, server_manager=manager),
            name="local service keepalive",
        )
        self._keepalive_task.add_done_callback(self._keepalive_task_done)
        observe("keepalive.started")

    async def stop_keepalive(self) -> None:
        """停止并回收当前后台保活任务。"""
        stop_event = self._keepalive_stop
        task = self._keepalive_task
        was_running = stop_event is not None or task is not None

        self._keepalive_stop = None
        self._keepalive_task = None

        if stop_event is not None:
            stop_event.set()
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if was_running:
            observe("keepalive.stopped")

    async def reboot(self) -> None:
        """重启已绑定的本地服务，并恢复保活任务。"""
        manager = self._manager
        if manager is None:
            raise AppError("Server manager is not bound")

        observe("helix.restart.start")
        await self.stop_keepalive()

        try:
            await manager.restart()
            ready = await manager.wait_until_ready(10.0, 0.3)
        finally:
            self.start_keepalive()

        if not ready:
            raise AppError("Server not ready after reboot")

        observe("helix.restart.complete")

    async def stop(self) -> None:
        """停止已绑定的本地服务进程，保留管理器供后续重启。"""
        manager = self._manager
        if manager is None:
            raise AppError("Server manager is not bound")

        observe("helix.stop.start")
        await self.stop_keepalive()
        await terminate_port_process(manager.port)
        observe("helix.stop.complete")

    async def close(self) -> None:
        """关闭启动任务、保活任务和本地服务管理器。"""
        await self.cancel_startup()
        await self.stop_keepalive()

        manager = self._manager
        terminate_on_close = self._terminate_on_close
        self._manager = None
        self._context = None
        self._terminate_on_close = False

        if manager is None:
            return None

        try:
            await manager.close()
        finally:
            if terminate_on_close:
                with contextlib.suppress(Exception):
                    await terminate_port_process(manager.port)

    @staticmethod
    def _keepalive_task_done(task: asyncio.Task[None]) -> None:
        """回收后台保活任务异常，避免事件循环输出未取回异常。"""
        if task.cancelled():
            return None

        try:
            error = task.exception()
        except asyncio.CancelledError:
            return None

        if error is not None:
            observe_exception("keepalive.task.failed", error, level="WARNING")


if __name__ == '__main__':
    pass
