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
    "McpServicesBusy",
)


McpAction = typing.Literal["start", "force", "stop", "restart", "status"]
McpConnectionState = typing.Literal["stopped", "starting", "ready", "stopping", "failed"]
McpTransport = typing.Literal["stdio", "streamable_http", "sse"]


class McpServicesBusy(RuntimeError):
    """报告使用范围仍占用的服务；拒绝交互关闭，不修改连接事实。"""

    def __init__(self, config_keys: tuple[str, ...]) -> None:
        """保存被占用的原始配置键以便调用方报告目标。"""
        self.config_keys = config_keys
        super().__init__("MCP services busy: " + ", ".join(config_keys))


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
        """原子校验使用占用并执行单服务动作，最终释放仍由 Harness 调用 stop。"""
        ...

    def use_tools(self, server: str | None = None) -> typing.ContextManager[ExternalToolGroupPort | None]:
        """借用冻结目录直至上下文退出；Harness 的 Turn 与 Hook 必须覆盖整个使用范围。

        server 是 Hook 的既有服务别名，空值借用全部已发布服务。实现方在同一同步步骤中
        冻结目录和记录引用，关闭检查与禁止新引用间不能让出执行权。
        """
        ...

    def retire(self) -> None:
        """禁止本实例接收新使用范围和发布启动结果；已借用范围由原消费者释放。"""
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
        defer_activity_stop: bool = False,
    ) -> None:
        """重启 MCP 连接。"""
        ...

    async def stop_services(self) -> None:
        """交互全停有占用时抛出 McpServicesBusy；关闭中取消仍等待回收，已完成则正常返回。"""
        ...

    async def stop(self) -> None:
        """最终释放时禁止新使用并等待既有范围退出，不能按交互 busy 拒绝关闭。"""
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
