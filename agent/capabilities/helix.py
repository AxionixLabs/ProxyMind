# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import inspect
import typing
from collections.abc import (
    Awaitable,
    Callable,
)

from agent.ports import (
    CapabilityError,
    HelixCapability,
    HelixState,
)

LifecycleCallback: typing.TypeAlias = Callable[
    [],
    None | Awaitable[None],
]


class InMemoryHelixCapability:
    """提供无进程副作用的 Helix 生命周期能力替身。"""

    def __init__(
        self,
        *,
        on_start: LifecycleCallback | None = None,
        on_restart: LifecycleCallback | None = None,
        on_stop: LifecycleCallback | None = None,
        on_close: LifecycleCallback | None = None,
    ) -> None:
        """绑定可选生命周期回调并初始化为 stopped。"""
        self._on_start = on_start
        self._on_restart = on_restart
        self._on_stop = on_stop
        self._on_close = on_close
        self._state: HelixState = "stopped"
        self._lock = asyncio.Lock()

    @property
    def state(self) -> HelixState:
        """返回当前 Helix 生命周期状态。"""
        return self._state

    async def ensure_ready(self, *, wait_sec: float = 10.0) -> None:
        """串行启动 Helix，并将启动失败归一化。"""
        if wait_sec <= 0:
            raise ValueError("Helix wait_sec must be positive")
        async with self._lock:
            self._require_not_closed()
            if self._state == "ready":
                return None
            self._state = "starting"
            try:
                await self._run_callback(self._on_start)
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
        """串行重启 Helix，并在成功后回到 ready。"""
        if wait_sec <= 0:
            raise ValueError("Helix wait_sec must be positive")
        async with self._lock:
            self._require_not_closed()
            self._state = "restarting"
            try:
                await self._run_callback(self._on_restart or self._on_start)
            except Exception as error:
                self._state = "failed"
                raise CapabilityError(
                    "helix_restart_failed",
                    str(error).strip() or "Helix restart failed",
                    retryable=True,
                    details={"exception_type": type(error).__name__},
                ) from error
            self._state = "ready"

    async def stop(self) -> None:
        """停止 Helix 并保留能力对象供后续启动。"""
        async with self._lock:
            self._require_not_closed()
            if self._state == "stopped":
                return None
            try:
                await self._run_callback(self._on_stop)
            except Exception as error:
                self._state = "failed"
                raise CapabilityError(
                    "helix_stop_failed",
                    str(error).strip() or "Helix stop failed",
                    details={"exception_type": type(error).__name__},
                ) from error
            self._state = "stopped"

    async def aclose(self) -> None:
        """幂等关闭 Helix 并禁止新的生命周期操作。"""
        async with self._lock:
            if self._state == "closed":
                return None
            try:
                await self._run_callback(self._on_close)
            except Exception as error:
                raise CapabilityError(
                    "helix_close_failed",
                    str(error).strip() or "Helix close failed",
                    details={"exception_type": type(error).__name__},
                ) from error
            finally:
                self._state = "closed"

    def _require_not_closed(self) -> None:
        """拒绝关闭后的生命周期操作。"""
        if self._state == "closed":
            raise CapabilityError("helix_closed", "Helix capability is closed")

    @staticmethod
    async def _run_callback(callback: LifecycleCallback | None) -> None:
        """执行同步或异步生命周期回调。"""
        if callback is None:
            return None
        result = callback()
        if inspect.isawaitable(result):
            await result


if not isinstance(InMemoryHelixCapability(), HelixCapability):
    raise TypeError("InMemoryHelixCapability must implement HelixCapability")


if __name__ == '__main__':
    pass
