# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable,
)
from dataclasses import (
    dataclass,
    field,
)
from pathlib import Path

from .tool_runtime import ExternalToolGroupPort

__all__ = (
    "McpRuntime",
    "McpConfigReader",
    "McpRuntimeContext",
    "McpRuntimeFactory",
    "McpRuntimeBuilder",
    "McpToolGroupSnapshot",
    "McpAction",
    "McpConnectionState",
    "McpTransport",
    "McpSingleService",
    "McpServiceControlRequest",
    "McpServiceSnapshot",
    "McpServiceOutcome",
)


McpAction = typing.Literal["start", "force", "stop", "restart", "status"]
McpConnectionState = typing.Literal["stopped", "starting", "ready", "stopping", "failed"]
McpTransport = typing.Literal["stdio", "streamable_http", "sse"]


@dataclass(frozen=True, slots=True)
class McpSingleService:
    """冻结原始配置键；取消导航和全量操作不属于此目标类型。"""

    config_key: str
    scope: typing.Literal["single"] = field(default="single", init=False)

    def __post_init__(self) -> None:
        """拒绝空目标，保留配置键本身的字节语义。"""
        if not isinstance(self.config_key, str) or not self.config_key.strip():
            raise ValueError("MCP service requires a non-empty configuration key")


@dataclass(frozen=True, slots=True)
class McpServiceControlRequest:
    """由调用方冻结目标上下文，运行时在执行前验证实例和工作区身份。"""

    runtime_id: str
    workspace: str
    action: McpAction
    target: McpSingleService

    def __post_init__(self) -> None:
        """拒绝尚未解析的动作和缺少身份的控制请求。"""
        if not isinstance(self.target, McpSingleService):
            raise TypeError("MCP service control requires a single service target")
        if self.action not in ("start", "force", "stop", "restart", "status"):
            raise ValueError("Unknown MCP service action")
        if not isinstance(self.runtime_id, str) or not self.runtime_id.strip() or not isinstance(self.workspace, str) or not self.workspace.strip():
            raise ValueError("MCP service control requires runtime and workspace identities")


@dataclass(frozen=True, slots=True)
class McpServiceSnapshot:
    """投影配置和单连接事实；连接归适配器所有，前端不得据此持有 SDK 资源。"""

    config_key: str
    tool_prefix: str
    config_enabled: bool | None
    state: McpConnectionState
    transport: McpTransport
    tools: tuple[str, ...] = ()
    discovered: int = 0
    filtered: int = 0
    connection_error: str | None = None


@dataclass(frozen=True, slots=True)
class McpServiceOutcome:
    """返回单目标操作结论；操作错误不覆盖仍健康的连接状态。"""

    config_key: str
    outcome: typing.Literal["applied", "unchanged", "disabled", "busy", "failed", "interrupted"]
    snapshot: McpServiceSnapshot | None
    operation_error: str | None = None


class McpRuntime(typing.Protocol):
    """定义 Harness 管理 MCP 生命周期所需的最小运行时端口。"""

    @property
    def runtime_id(self) -> str:
        """返回本实例身份，关闭或移交后不能复用于新工作区。"""
        ...

    @property
    def workspace(self) -> Path:
        """返回创建运行时时冻结的工作区。"""
        ...

    @property
    def service_snapshots(self) -> tuple[McpServiceSnapshot, ...]:
        """读取配置与连接的本地投影，不探测远端健康状态。"""
        ...

    async def control_service(self, request: McpServiceControlRequest) -> McpServiceOutcome:
        """在生命周期锁内执行单服务动作；调用方须先收束目标工具消费者，最终释放仍由 Harness 调用 stop。"""
        ...

    @property
    def started(self) -> bool:
        """返回外部 MCP 是否已经建立可用连接。"""
        ...

    @property
    def group(self) -> ExternalToolGroupPort | None:
        """返回本实例持有的工具组；组可以没有可用连接，尚未创建时返回空。"""
        ...

    @property
    def last_start_snapshot(self) -> dict[str, typing.Any]:
        """返回最近一次启动的不可变展示快照。"""
        ...

    @property
    def tool_groups(self) -> tuple["McpToolGroupSnapshot", ...]:
        """返回与具体 MCP SDK 对象解耦的工具分组状态。"""
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


@dataclass(frozen=True, slots=True)
class McpToolGroupSnapshot:
    """描述一个外部 MCP 服务向前端暴露的稳定状态投影。"""

    server: str
    transport: str
    auth: str
    tools: tuple[str, ...]
    discovered: int
    exposed: int
    filtered: int


class McpConfigReader(typing.Protocol):
    """定义 MCP 运行时读取有效配置所需的端口。"""

    @property
    def workspace(self) -> Path:
        """返回与配置同属一个活动上下文的工作目录。"""
        ...

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
