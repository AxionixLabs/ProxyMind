# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import httpx
import asyncio
import uvicorn
import contextlib
from loguru import logger
from engine.errors import ApplicationError
from engine.ports import port_available
from .app import create_app
from .endpoints import (
    DEFAULT_CONFIG_SERVICE_HOST,
    DEFAULT_CONFIG_SERVICE_PORT,
    config_service_endpoints
)


class ConfigServiceRuntime(object):
    """管理进程内配置服务生命周期。"""

    def __init__(
        self,
        *,
        host: str = DEFAULT_CONFIG_SERVICE_HOST,
        preferred_port: int = DEFAULT_CONFIG_SERVICE_PORT,
        port_scan_limit: int = 50,
        log_level: str = "INFO"
    ) -> None:
        self.host            = str(host or DEFAULT_CONFIG_SERVICE_HOST)
        self.preferred_port  = int(preferred_port)
        self.port_scan_limit = max(1, int(port_scan_limit))
        self.log_level       = str(log_level or "INFO").lower()
        self.port            = self.preferred_port
        self.base_url        = f"http://{self.host}:{self.port}"

        self.server: uvicorn.Server | None   = None
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动配置服务，端口被占用时自动顺延。"""
        if self.task is not None and not self.task.done():
            return None

        self.port = await self._find_available_port()
        self.base_url = config_service_endpoints.configure(
            host=self.host,
            port=self.port
        )

        config = uvicorn.Config(
            app=create_app(),
            host=self.host,
            port=self.port,
            log_level="critical",
            access_log=False,
            lifespan="on"
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(
            self.server.serve(),
            name="config service"
        )
        self.task.add_done_callback(self._task_done)
        await self.wait_until_ready()

    async def stop(self) -> None:
        """停止配置服务。"""
        server = self.server
        task   = self.task

        self.server = None
        self.task   = None

        if server is not None:
            server.should_exit = True

        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def wait_until_ready(self, *, wait_sec: float = 5.0, interval: float = 0.1) -> None:
        """等待配置服务就绪。"""
        deadline = time.monotonic() + max(0.5, float(wait_sec))
        while time.monotonic() < deadline:
            task = self.task
            if task is not None and task.done():
                if task.cancelled():
                    raise ApplicationError("Config service stopped before ready")
                error = task.exception()
                if error is not None:
                    raise ApplicationError(f"Config service stopped: {type(error).__name__}: {error}") from error
                raise ApplicationError("Config service stopped before ready")

            if await self.probe_ready():
                return None
            await asyncio.sleep(interval)

        raise ApplicationError("Config service not ready")

    async def probe_ready(self) -> bool:
        """探测配置服务是否已响应。"""
        try:
            async with httpx.AsyncClient(timeout=0.6, trust_env=False) as client:
                response = await client.get(f"{self.base_url}/ready")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, TypeError, ValueError):
            return False
        return isinstance(payload, dict) and payload.get("ready") is True

    async def _find_available_port(self) -> int:
        """查找配置服务可用端口，不占用 Helix 默认端口 3333。"""
        end_port = self.preferred_port + self.port_scan_limit
        for port in range(self.preferred_port, end_port):
            if await port_available(port, host=self.host):
                return port

        raise ApplicationError(
            f"Config service has no available port from "
            f"{self.preferred_port} to {end_port - 1}"
        )

    @staticmethod
    def _task_done(task: asyncio.Task[None]) -> None:
        """记录配置服务异常退出。"""
        if task.cancelled():
            return None
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return None
        if error is not None:
            logger.debug(f"[ConfigService] stopped: {type(error).__name__}: {error}")


if __name__ == "__main__":
    pass
