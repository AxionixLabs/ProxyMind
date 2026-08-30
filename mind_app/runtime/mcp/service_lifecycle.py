# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from agent.application import (
    CapabilityError,
    HelixCapability,
    HelixState,
)
from engine.errors import AppError
from engine.manage import ServerManage
from observability import (
    observe,
    observe_exception,
)
from engine.ports import terminate_port_process
from .keepalive import run_keepalive

if typing.TYPE_CHECKING:
    from .service_runtime import ServiceRuntimeContext

ServiceStartupOperation = typing.Callable[[], typing.Awaitable[bool]]


class ServerManageHelixCapability:
    """把本地 ServerManage 生命周期适配到 HelixCapability 端口。"""

    def __init__(self, manager: ServerManage) -> None:
        """绑定一个服务管理器并初始化能力状态。"""
        self._manager = manager
        self._state: HelixState = "stopped"

    @property
    def state(self) -> HelixState:
        """返回当前服务能力状态。"""
        return self._state

    async def ensure_ready(self, *, wait_sec: float = 10.0) -> None:
        """启动服务并等待健康检查通过。"""
        if wait_sec <= 0:
            raise ValueError("Helix wait_sec must be positive")
        if self._state == "closed":
            raise CapabilityError("helix_closed", "Helix capability is closed")
        if self._state == "ready":
            return None
        self._state = "starting"
        try:
            await self._manager.ensure_running(wait_sec=wait_sec)
        except Exception as error:
            self._state = "failed"
            raise CapabilityError(
                "helix_start_failed",
                str(error).strip() or "Helix startup failed",
                retryable=True,
                details={"exception_type": type(error).__name__},
            ) from error
        self._state = "ready"

    async def restart(self, *, wait_sec: float = 10.0) -> None:
        """重启服务并等待健康检查通过。"""
        if wait_sec <= 0:
            raise ValueError("Helix wait_sec must be positive")
        if self._state == "closed":
            raise CapabilityError("helix_closed", "Helix capability is closed")
        self._state = "restarting"
        try:
            await self._manager.restart()
            ready = await self._manager.wait_until_ready(wait_sec, 0.3)
        except Exception as error:
            self._state = "failed"
            raise CapabilityError(
                "helix_restart_failed",
                str(error).strip() or "Helix restart failed",
                retryable=True,
                details={"exception_type": type(error).__name__},
            ) from error
        if not ready:
            self._state = "failed"
            raise CapabilityError(
                "helix_restart_failed",
                "Helix was not ready after restart",
                retryable=True,
            )
        self._state = "ready"

    async def stop(self) -> None:
        """停止服务进程但保留管理器。"""
        if self._state == "closed":
            raise CapabilityError("helix_closed", "Helix capability is closed")
        try:
            await terminate_port_process(self._manager.port)
        except Exception as error:
            self._state = "failed"
            raise CapabilityError(
                "helix_stop_failed",
                str(error).strip() or "Helix stop failed",
                retryable=True,
                details={"exception_type": type(error).__name__},
            ) from error
        self._state = "stopped"

    async def aclose(self) -> None:
        """关闭服务管理器并释放其 HTTP 客户端。"""
        if self._state == "closed":
            return None
        try:
            await self._manager.close()
        except Exception as error:
            self._state = "closed"
            raise CapabilityError(
                "helix_close_failed",
                str(error).strip() or "Helix close failed",
                details={"exception_type": type(error).__name__},
            ) from error
        self._state = "closed"
class ServiceRuntimeOwner(object):
    """持有本地服务管理器，并管理启动、保活与最终释放。"""

    def __init__(self) -> None:
        """初始化未绑定的服务运行时状态。"""
        self._manager: ServerManage | None = None
        self._capability: HelixCapability | None = None
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
        *,
        capability: HelixCapability | None = None,
    ) -> None:
        """绑定本地服务管理器和对应准备上下文。"""
        if capability is not None and not isinstance(capability, HelixCapability):
            raise TypeError("Helix capability is invalid")
        self._manager = manager
        self._context = context
        self._capability = capability

    async def ensure_ready(self, *, wait_sec: float = 10.0) -> None:
        """通过 Helix 能力端口确保本地服务就绪。"""
        capability = self._capability
        if capability is not None:
            await capability.ensure_ready(wait_sec=wait_sec)
            return None
        manager = self._manager
        if manager is None:
            raise AppError("Runtime manager is not bound")
        await manager.ensure_running(wait_sec=wait_sec)

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
        ready = False

        try:
            capability = self._capability
            if capability is not None:
                await capability.restart(wait_sec=10.0)
                ready = True
            else:
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
        capability = self._capability
        if capability is not None:
            await capability.stop()
        else:
            await terminate_port_process(manager.port)
        observe("helix.stop.complete")

    async def close(self) -> None:
        """关闭启动任务、保活任务和本地服务管理器。"""
        await self.cancel_startup()
        await self.stop_keepalive()

        manager = self._manager
        capability = self._capability
        terminate_on_close = self._terminate_on_close
        self._manager = None
        self._context = None
        self._capability = None
        self._terminate_on_close = False

        if manager is None:
            return None

        try:
            if capability is not None:
                await capability.aclose()
            else:
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
