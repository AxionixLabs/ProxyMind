# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
from collections.abc import (
    Awaitable,
    Callable,
)

from agent.domain.tool_policy import ToolFilterMode
from agent.harness.mcp.owner import McpRuntimeOwner
from agent.ports import (
    BeforeToolSession,
    ExternalToolGroupPort,
    McpRuntimeFactory,
    McpSessionPort,
    ToolRegistryPort,
    ToolRuntimeBuilder,
    ToolRuntimePort,
    ToolRuntimeSources,
    TurnEventReportingPort,
)
from observability import observe

__all__ = ("ExecutionResources",)


SessionResult = typing.TypeVar("SessionResult")
CleanupResult = typing.TypeVar("CleanupResult")
RegistryFactory: typing.TypeAlias = Callable[[], ToolRegistryPort]
CleanupWaiter: typing.TypeAlias = Callable[
    [Awaitable[CleanupResult]],
    Awaitable[CleanupResult],
]


def _unconfigured_mcp_runtime() -> typing.NoReturn:
    """拒绝在组合根未提供 MCP 能力时隐式创建实现。"""
    raise RuntimeError("MCP runtime factory is required")


def _normalize_tool_profile(value: str) -> ToolFilterMode:
    """校验并返回服务工具档位。"""
    if value == "app":
        return "app"
    if value == "api":
        return "api"
    raise ValueError(f"Invalid Helix tool profile: {value}")


class ExecutionResources:
    """持有 Turn 执行依赖的动态工具、MCP 和事件报告资源。

    本对象在应用宿主生命周期内唯一存在。工具注册表只在一次 Turn 建立工具会话时
    读取，工作区切换可以原子替换后续 Turn 使用的客户端注册表；已经运行的 Turn
    继续使用自己的会话快照。具体注册表和 MCP 实现必须由组合根注入。
    """

    def __init__(
        self,
        *,
        event_reporting: TurnEventReportingPort,
        tool_runtime_builder: ToolRuntimeBuilder,
        client_registry_factory: RegistryFactory,
        builtin_registry_factory: RegistryFactory,
        external_runtime_factory: McpRuntimeFactory | None,
        await_cleanup: CleanupWaiter,
    ) -> None:
        """绑定资源工厂，但延迟创建依赖完整应用图的本地工具注册表。"""
        if not callable(tool_runtime_builder):
            raise TypeError("tool runtime factory is required")
        if not callable(client_registry_factory):
            raise TypeError("client tool registry factory is required")
        if not callable(builtin_registry_factory):
            raise TypeError("builtin tool registry factory is required")
        if not callable(await_cleanup):
            raise TypeError("cleanup waiter is required")

        self.event_reporting = event_reporting
        self.external_mcp = McpRuntimeOwner(
            runtime_factory=(
                external_runtime_factory
                if external_runtime_factory is not None
                else _unconfigured_mcp_runtime
            ),
        )
        self._client_registry_factory = client_registry_factory
        self._builtin_registry_factory = builtin_registry_factory
        self._client_registry: ToolRegistryPort | None = None
        self._builtin_registry: ToolRegistryPort | None = None
        self._service_linked = False
        self._service_tool_profile: ToolFilterMode | None = None
        self._service_exec_env: dict[str, typing.Any] | None = None
        self._await_cleanup = await_cleanup

        self._tool_runtime: ToolRuntimePort = tool_runtime_builder(
            ToolRuntimeSources(
                client_registry=self.client_registry,
                builtin_registry=self.builtin_registry,
                external_group=self._current_external_tool_group,
                service_linked=self.is_service_linked,
            )
        )

    def client_registry(self) -> ToolRegistryPort:
        """返回当前客户端工具注册表，并在首次读取时完成构建。"""
        registry = self._client_registry
        if registry is None:
            registry = self._client_registry_factory()
            self._client_registry = registry
        return registry

    def builtin_registry(self) -> ToolRegistryPort:
        """返回当前 Harness 内置工具注册表。"""
        registry = self._builtin_registry
        if registry is None:
            registry = self._builtin_registry_factory()
            self._builtin_registry = registry
        return registry

    def rebuild_client_registry(self) -> None:
        """为后续 Turn 重建绑定当前工作区的客户端工具注册表。"""
        self._client_registry = self._client_registry_factory()

    def client_tool_count(self) -> int:
        """返回当前客户端工具目录的工具数量。"""
        return len(self.client_registry().list_tools().tools)

    def link_service(
        self,
        exec_env: dict[str, typing.Any] | None = None,
        *,
        tool_profile: ToolFilterMode = "app",
    ) -> None:
        """把已就绪的本地服务链接到后续工具会话。"""
        normalized = _normalize_tool_profile(tool_profile)
        self._service_linked = True
        self._service_tool_profile = normalized
        self._service_exec_env = (
            copy.deepcopy(exec_env)
            if isinstance(exec_env, dict)
            else None
        )
        observe(
            "helix.linked",
            tool_profile=normalized,
            exec_env=bool(self._service_exec_env),
        )

    def set_service_tool_profile(self, tool_profile: ToolFilterMode) -> None:
        """切换已经链接的本地服务工具档位。"""
        if not self._service_linked:
            raise RuntimeError("Helix MCP is not linked")
        normalized = _normalize_tool_profile(tool_profile)
        self._service_tool_profile = normalized
        observe("helix.tool_profile.changed", tool_profile=normalized)

    def unlink_service(self) -> None:
        """解除本地服务与工具会话的链接，但不停止服务进程。"""
        was_linked = self._service_linked
        self._service_linked = False
        self._service_tool_profile = None
        self._service_exec_env = None
        if was_linked:
            observe("helix.unlinked")

    def is_service_linked(self) -> bool:
        """返回本地服务是否已链接到工具会话。"""
        return self._service_linked

    def tool_profile_for_turn(self) -> ToolFilterMode | None:
        """返回后续 Turn 使用的本地服务工具档位。"""
        if not self._service_linked:
            return None
        profile = self._service_tool_profile
        if profile is None:
            raise RuntimeError("Helix tool profile is not selected")
        return profile

    def service_exec_env_snapshot(self) -> dict[str, typing.Any] | None:
        """返回本地服务执行环境的独立快照。"""
        environment = self._service_exec_env
        if environment is None:
            return None
        return copy.deepcopy(environment)

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        function: Callable[
            [McpSessionPort, list[dict[str, typing.Any]]],
            Awaitable[SessionResult],
        ],
        before_user_flow: BeforeToolSession | None = None,
    ) -> SessionResult:
        """建立包含当前动态工具快照的 MCP 会话并执行回调。"""
        return await self._tool_runtime.with_session(
            pref_config,
            function,
            before_user_flow=before_user_flow,
        )

    async def await_cleanup(
        self,
        awaitable: Awaitable[CleanupResult],
    ) -> CleanupResult:
        """在取消态下等待执行资源完成清理。"""
        return await self._await_cleanup(awaitable)

    async def close(self) -> None:
        """关闭事件报告与外部 MCP，并清除非持久服务链接状态。"""
        self.unlink_service()
        await self.event_reporting.close()
        await self.external_mcp.close()

    def _current_external_tool_group(self) -> ExternalToolGroupPort | None:
        """返回当前外部 MCP 已发布的工具组。"""
        runtime = self.external_mcp.current
        return runtime.group if runtime is not None else None


if __name__ == '__main__':
    pass
