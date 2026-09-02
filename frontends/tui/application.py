# -*- coding: utf-8 -*-

import typing
from collections.abc import (
    Awaitable,
    Callable,
)
from pathlib import Path

from agent.application.agents.views import AgentSnapshot
from agent.domain.policies import PermissionSettings
from agent.domain.tool_policy import ToolFilterMode
from agent.ports import (
    AttachmentStatePort,
    BeforeToolSession,
    FrontendActivityPort,
    McpRuntime,
    McpSessionPort,
    ProcessLifecyclePort,
    RootConversationPort,
    SubscriptionRuntime,
    WorkspaceRuntime,
)
from frontends.runtime import Frontend
from infrastructure.config.session import ConfigSession
from infrastructure.services.runtime_context import ServiceRuntimeContext
from infrastructure.services.server_manager import ServerManage

SessionResult = typing.TypeVar("SessionResult")


class TuiSettingsPort(typing.Protocol):
    """定义 TUI 可读取和修改的进程设置边界。"""

    config: ConfigSession
    permissions: PermissionSettings

    def preference_config(self) -> dict[str, typing.Any]:
        """返回当前有效偏好快照。"""
        ...

    def apply_permissions(
        self,
        settings: PermissionSettings,
    ) -> PermissionSettings:
        """持久化并返回有效权限。"""
        ...

    async def refresh_preferences_if_stale(
        self,
        *,
        ttl_sec: float | None = None,
    ) -> None:
        """按刷新窗口更新偏好。"""
        ...

    async def fresh_preferences(
        self,
        *,
        ttl_sec: float | None = None,
    ) -> dict[str, typing.Any]:
        """刷新并返回有效偏好快照。"""
        ...


class ExternalMcpOwnerPort(typing.Protocol):
    """定义 TUI 观察外部 MCP 连接所需的 owner 边界。"""

    current: McpRuntime | None

    async def start(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """启动或复用当前 MCP 运行时。"""
        ...

    async def restart(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False,
    ) -> None:
        """重启当前 MCP 运行时。"""
        ...

    async def close(self) -> None:
        """关闭并释放当前 MCP 运行时。"""
        ...


class TuiExecutionResourcesPort(typing.Protocol):
    """定义 TUI 工具与本地服务操作所需的执行资源边界。"""

    external_mcp: ExternalMcpOwnerPort

    def is_service_linked(self) -> bool:
        """返回本地服务是否已链接到工具会话。"""
        ...

    def tool_profile_for_turn(self) -> ToolFilterMode | None:
        """返回后续 Turn 使用的本地服务工具档位。"""
        ...

    def set_service_tool_profile(self, tool_profile: ToolFilterMode) -> None:
        """切换已链接的本地服务工具档位。"""
        ...

    def unlink_service(self) -> None:
        """解除本地服务工具链接。"""
        ...

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        function: Callable[
            [McpSessionPort, list[dict[str, typing.Any]]],
            Awaitable[SessionResult],
        ],
        before_user_flow: BeforeToolSession | None = None,
    ) -> SessionResult:
        """使用当前工具目录运行一个短期 MCP 会话。"""
        ...


class TuiServiceRuntimePort(typing.Protocol):
    """定义 TUI 管理可选本地服务所需的生命周期。"""

    manager: ServerManage | None

    async def cancel_startup(self) -> None:
        """取消未完成的服务启动。"""
        ...

    def require_context(self) -> ServiceRuntimeContext:
        """返回已绑定的服务上下文。"""
        ...

    def request_termination_on_close(self) -> None:
        """要求进程退出时终止服务。"""
        ...

    async def stop(self) -> None:
        """停止已绑定的服务。"""
        ...


class TuiSubscriptionOwnerPort(typing.Protocol):
    """定义 TUI 订阅监听器的启动、观察和暂停边界。"""

    current: SubscriptionRuntime | None

    def start(self) -> SubscriptionRuntime:
        """启动或复用订阅监听器。"""
        ...

    async def pause(self) -> None:
        """暂停订阅传输并保留收件箱。"""
        ...


class TuiSubagentPort(typing.Protocol):
    """定义 TUI 子执行线程管理界面的读取和控制边界。"""

    async def get(
        self,
        root_session_id: str,
        target: str,
    ) -> AgentSnapshot:
        """返回目标执行线程快照。"""
        ...

    async def snapshots(
        self,
        root_session_id: str,
    ) -> tuple[AgentSnapshot, ...]:
        """返回根会话全部执行线程快照。"""
        ...

    async def interrupt(
        self,
        root_session_id: str,
        target: str,
    ) -> AgentSnapshot:
        """中断目标执行线程。"""
        ...

    async def resume(
        self,
        root_session_id: str,
        target: str,
    ) -> AgentSnapshot:
        """恢复目标执行线程。"""
        ...

    async def close(
        self,
        root_session_id: str,
        target: str,
    ) -> AgentSnapshot:
        """关闭目标执行线程。"""
        ...


class TuiApplicationHost(typing.Protocol):
    """定义 TUI 会话可消费的应用宿主端口集合。

    该契约属于前端适配器，不拥有任何 Harness 状态。具体组合根可以同时满足 CLI、
    MCP 和 TUI 契约，TUI 模块不得导入该具体实现。
    """

    activity: FrontendActivityPort
    attach: AttachmentStatePort
    conversation: RootConversationPort
    execution: TuiExecutionResourcesPort
    frontend: Frontend
    history_workspace: str
    lifecycle: ProcessLifecyclePort
    service_runtime: TuiServiceRuntimePort
    settings: TuiSettingsPort
    subagents: TuiSubagentPort
    subscription: TuiSubscriptionOwnerPort
    workspace_runtime: WorkspaceRuntime

    def set_history_workspace(self, workspace: str | Path) -> str:
        """切换后续 Turn 使用的工作区资源。"""
        ...


__all__ = (
    "ExternalMcpOwnerPort",
    "TuiApplicationHost",
    "TuiExecutionResourcesPort",
    "TuiServiceRuntimePort",
    "TuiSettingsPort",
    "TuiSubagentPort",
    "TuiSubscriptionOwnerPort",
)
