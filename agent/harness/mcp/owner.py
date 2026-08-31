# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from collections.abc import Awaitable
from agent.ports.mcp_runtime import (
    McpRuntime,
    McpRuntimeFactory,
)


class McpRuntimeOwner(object):
    """持有 MCP 运行时，并管理启动、重启和最终释放语义。"""

    def __init__(self, *, runtime_factory: McpRuntimeFactory) -> None:
        """绑定由组合根提供的 MCP 运行时工厂。"""
        if not callable(runtime_factory):
            raise TypeError("MCP runtime factory must be callable")
        self._runtime_factory = runtime_factory
        self._runtime: McpRuntime | None = None

    @property
    def current(self) -> McpRuntime | None:
        """返回当前持有的 MCP 运行时。"""
        return self._runtime

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
        """解除实例所有权，并在取消态下等待运行时完成清理。"""
        runtime = self._runtime
        self._runtime = None
        if runtime is not None:
            await self._await_cleanup(runtime.stop())

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
