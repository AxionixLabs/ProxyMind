# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable,
)
from dataclasses import dataclass

from .tool_runtime import ExternalToolGroupPort

__all__ = (
    "McpRuntime",
    "McpConfigReader",
    "McpRuntimeContext",
    "McpRuntimeFactory",
    "McpRuntimeBuilder",
)


class McpRuntime(typing.Protocol):
    """定义 Harness 管理 MCP 生命周期所需的最小运行时端口。"""

    @property
    def group(self) -> ExternalToolGroupPort | None:
        """返回已建立的外部工具组，尚不可用时返回空。"""
        ...

    async def start(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """启动 MCP 连接。"""
        ...

    async def restart(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """重启 MCP 连接。"""
        ...

    async def stop(self) -> None:
        """停止 MCP 连接并释放资源。"""
        ...


class McpConfigReader(typing.Protocol):
    """定义 MCP 运行时读取有效配置所需的端口。"""

    def load(self) -> dict[str, typing.Any]:
        """返回当前有效配置快照。"""
        ...


class McpActivityStopper(typing.Protocol):
    """定义外部 MCP 结束活动展示所需的回调。"""

    async def __call__(
        self,
        kind: str | None = None,
        *,
        settle: bool = True,
    ) -> None:
        """结束指定类型的活动展示。"""
        ...


@dataclass(frozen=True, slots=True)
class McpRuntimeContext:
    """冻结具体 MCP runtime 使用的配置与生命周期回调。"""

    config: McpConfigReader
    start_activity: Callable[
        [Callable[[], dict[str, typing.Any]]],
        Awaitable[None],
    ]
    stop_activity: McpActivityStopper
    await_cleanup: Callable[[Awaitable[None]], Awaitable[None]]

    def __post_init__(self) -> None:
        """拒绝缺失的生命周期依赖。"""
        callbacks = (
            self.start_activity,
            self.stop_activity,
            self.await_cleanup,
        )
        if not all(callable(callback) for callback in callbacks):
            raise TypeError("MCP runtime callbacks must be callable")


McpRuntimeFactory: typing.TypeAlias = Callable[[], McpRuntime]
McpRuntimeBuilder: typing.TypeAlias = Callable[[McpRuntimeContext], McpRuntime]


if __name__ == '__main__':
    pass
