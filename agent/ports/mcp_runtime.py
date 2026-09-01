# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable
)
from .tool_runtime import ExternalToolGroupPort

__all__ = (
    "McpRuntime",
    "McpConfigReader",
    "McpRuntimeHost",
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


class McpRuntimeHost(typing.Protocol):
    """定义具体 MCP 运行时使用的应用生命周期端口。"""

    config_session: McpConfigReader

    async def start_external_mcp_anim(
        self,
        snapshot: Callable[[], dict[str, typing.Any]],
    ) -> None:
        """开始展示外部 MCP 启动状态。"""
        ...

    async def stop_anim(
        self,
        kind: str | None = None,
        *,
        settle: bool = True,
    ) -> None:
        """结束指定类型的活动展示。"""
        ...

    async def await_cleanup(self, awaitable: Awaitable[None]) -> None:
        """等待异步资源清理完成。"""
        ...


McpRuntimeFactory: typing.TypeAlias = Callable[[], McpRuntime]
McpRuntimeBuilder: typing.TypeAlias = Callable[[McpRuntimeHost], McpRuntime]


if __name__ == '__main__':
    pass
