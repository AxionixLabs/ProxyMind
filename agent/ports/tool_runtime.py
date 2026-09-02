# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import dataclass
from datetime import timedelta

from .mcp_session import McpSessionPort

if typing.TYPE_CHECKING:
    from mcp import types as mcp_types
    from mcp.shared.session import ProgressFnT
    from agent.application.turns.context import TurnContext

SessionResult = typing.TypeVar("SessionResult")

ToolSessionCallback: typing.TypeAlias = Callable[
    [
        McpSessionPort,
        list[dict[str, typing.Any]],
    ],
    Awaitable[SessionResult],
]
BeforeToolSession: typing.TypeAlias = Callable[[], Awaitable[None] | None]
ToolSessionFactory: typing.TypeAlias = Callable[
    [],
    typing.AsyncContextManager[McpSessionPort],
]


class ToolRegistryPort(typing.Protocol):
    """定义本地工具注册表的发现与调用边界。"""

    def has_tool(self, name: str) -> bool:
        """判断注册表是否持有指定工具。"""
        ...

    def list_tools(self) -> "mcp_types.ListToolsResult":
        """返回当前注册表的 MCP 兼容工具描述。"""
        ...

    async def call_tool(
        self,
        session: McpSessionPort,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        *,
        read_timeout_seconds: timedelta | None = None,
        progress_callback: "ProgressFnT | None" = None,
        meta: dict[str, typing.Any] | None = None,
        call_id: str | None = None,
        turn_context: "TurnContext | None" = None,
        pref_config: Mapping[str, typing.Any] | None = None,
    ) -> "mcp_types.CallToolResult":
        """在给定会话和 Turn 上下文中分发本地工具。"""
        ...


class ExternalToolGroupPort(typing.Protocol):
    """定义外部 MCP 聚合连接向工具会话暴露的能力。"""

    @property
    def tools(self) -> Mapping[str, "mcp_types.Tool"]:
        """返回按模型侧名称索引的外部工具快照。"""
        ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, typing.Any] | None = None,
        read_timeout_seconds: timedelta | None = None,
        progress_callback: "ProgressFnT | None" = None,
        *,
        meta: dict[str, typing.Any] | None = None,
    ) -> "mcp_types.CallToolResult":
        """调用已登记的外部 MCP 工具。"""
        ...


class ToolRuntimePort(typing.Protocol):
    """定义一次模型 Turn 获取组合工具会话的生命周期入口。"""

    async def with_session(
        self,
        pref_config: dict[str, typing.Any],
        function: ToolSessionCallback[SessionResult],
        before_user_flow: BeforeToolSession | None = None,
    ) -> SessionResult:
        """建立工具会话，在其生命周期内执行调用方用例。"""
        ...


@dataclass(frozen=True, slots=True)
class ToolRuntimeSources:
    """绑定工具运行时读取动态来源所需的最小 provider。

    provider 必须返回当前 Controller 生命周期内的权威实例；运行时只在一次会话开始时
    读取一次，从而让该 Turn 使用稳定快照。
    """

    client_registry: Callable[[], ToolRegistryPort]
    builtin_registry: Callable[[], ToolRegistryPort | None]
    external_group: Callable[[], ExternalToolGroupPort | None]
    service_linked: Callable[[], bool]

    def __post_init__(self) -> None:
        """在组合边界拒绝缺失或不可调用的 provider。"""
        providers = (
            self.client_registry,
            self.builtin_registry,
            self.external_group,
            self.service_linked,
        )
        if not all(callable(provider) for provider in providers):
            raise TypeError("tool runtime source providers must be callable")


ToolRuntimeBuilder: typing.TypeAlias = Callable[
    [ToolRuntimeSources],
    ToolRuntimePort,
]

__all__ = (
    "BeforeToolSession",
    "ExternalToolGroupPort",
    "ToolRegistryPort",
    "ToolRuntimeBuilder",
    "ToolRuntimePort",
    "ToolRuntimeSources",
    "ToolSessionCallback",
    "ToolSessionFactory",
)

if __name__ == '__main__':
    pass
