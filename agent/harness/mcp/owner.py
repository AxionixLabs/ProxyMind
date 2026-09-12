# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from collections.abc import Awaitable

from agent.ports.mcp_runtime import (
    McpRuntime,
    McpRuntimeFactory,
    McpServiceControlRequest,
    McpServiceOutcome,
)


class McpRuntimeOwner:
    """持有 MCP 运行时，并管理启动、重启和最终释放语义。"""

    def __init__(self, *, runtime_factory: McpRuntimeFactory) -> None:
        """绑定由组合根提供的 MCP 运行时工厂。"""
        if not callable(runtime_factory):
            raise TypeError("MCP runtime factory must be callable")
        self._runtime_factory = runtime_factory
        self._runtime: McpRuntime | None = None
        self._retiring: list[McpRuntime] = []
        self._close_lock = asyncio.Lock()

    @property
    def current(self) -> McpRuntime | None:
        """返回当前持有的 MCP 运行时。"""
        return self._runtime

    async def control_service(self, request: McpServiceControlRequest) -> McpServiceOutcome:
        """把单服务请求交给仍由本 owner 持有的实例，失效目标不创建新实例。"""
        runtime = self._runtime
        if runtime is None:
            return McpServiceOutcome(
                request.target.config_key, "failed", None, "MCP runtime is no longer active",
            )
        return await runtime.control_service(request)

    async def start(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """启动或复用当前 MCP 运行时。"""
        runtime = self._runtime
        if runtime is None:
            runtime = self._create_runtime()
            self._runtime = runtime
        await runtime.start(
            include_disabled=include_disabled,
            defer_activity_stop=defer_activity_stop,
        )

    async def restart(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """重启当前 MCP 运行时，尚未创建时先建立实例。"""
        runtime = self._runtime
        if runtime is None:
            runtime = self._create_runtime()
            self._runtime = runtime
        await runtime.restart(
            include_disabled=include_disabled,
            defer_activity_stop=defer_activity_stop,
        )

    async def close(self) -> None:
        """移出活动实例并完成释放，失败资源仍由本 owner 持有以供重试。"""
        async with self._close_lock:
            runtime = self.detach()
            if runtime is not None:
                self._retiring.append(runtime)
            for pending in tuple(self._retiring):
                await self._await_cleanup(self._retire(pending))

    async def _retire(self, runtime: McpRuntime) -> None:
        """只在实际资源释放成功后移除待清理实例。"""
        await runtime.stop()
        self._retiring.remove(runtime)

    def detach(self) -> McpRuntime | None:
        """移交旧实例所有权，使后续工具会话无法读取旧工作区工具。"""
        runtime = self._runtime
        self._runtime = None
        return runtime

    def _create_runtime(self) -> McpRuntime:
        """使用组合根工厂创建 MCP 运行时。"""
        return self._runtime_factory()

    @staticmethod
    async def _await_cleanup(awaitable: Awaitable[None]) -> None:
        """在调用方取消时仍等待 MCP 资源完成释放。"""
        task = asyncio.ensure_future(awaitable)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise


if __name__ == '__main__':
    pass
