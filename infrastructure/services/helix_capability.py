# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports import (
    CapabilityError,
    HelixState,
)
from infrastructure.platform.ports import terminate_port_process
from infrastructure.services.server_manager import ServerManage


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


__all__ = ("ServerManageHelixCapability",)
