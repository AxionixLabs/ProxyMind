# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from collections.abc import Callable

from agent.harness.mcp.owner import McpRuntimeOwner
from agent.ports.mcp_runtime import McpRuntime
from agent.ports.workspace import (
    WorkspaceResources,
    WorkspaceRuntime,
)


class WorkspaceChange:
    """持有一次准备、提交和收尾；会话结束前不替换活动能力。"""

    def __init__(
        self,
        owner: WorkspaceRuntime,
        resources: WorkspaceResources,
        *,
        publish: Callable[[], None],
        external_mcp: McpRuntimeOwner,
    ) -> None:
        """接收已校验的资源和只执行赋值的组合回调。"""
        self._owner = owner
        self._resources = resources
        self._publish = publish
        self._external_mcp = external_mcp
        self._old_mcp: McpRuntime | None = None
        self._committed = False
        self._finished = False

    def commit(self) -> None:
        """在同一个同步步骤中替换资源并发布已准备的配置依赖。"""
        if self._committed or self._finished:
            raise RuntimeError("workspace change is no longer pending")
        self._resources = self._owner.activate(self._resources)
        self._publish()
        self._old_mcp = self._external_mcp.detach()
        self._committed = True

    async def finish(self) -> tuple[str, ...]:
        """等待资源收敛，成功提交后的清理错误作为警告返回。"""
        if self._finished:
            return ()
        self._finished = True
        operations = [self._resources.coding.close()]
        if self._old_mcp is not None:
            operations.append(self._old_mcp.stop())
        results = await asyncio.gather(*operations, return_exceptions=True)
        errors = [str(result) for result in results if isinstance(result, Exception)]
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
        if self._old_mcp is not None:
            try:
                await self._external_mcp.start()
            except Exception as error:
                errors.append(f"MCP startup: {error}")
        return tuple(errors)


if __name__ == '__main__':
    pass
