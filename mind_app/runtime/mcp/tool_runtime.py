# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import inspect
import asyncio
import contextlib
from engine.observability import observe_exception
from mind_app.mcp.contracts import McpSessionLike
from mind_app.mcp.tools import build_tool_context
from .local import open_local_mcp_session

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind

SessionCallback = typing.Callable[
    [
        McpSessionLike,
        list[dict[str, typing.Any]],
    ],
    typing.Awaitable[None]
]


class ToolRuntime(typing.Protocol):
    """定义工具运行时的会话入口。"""

    async def with_session(
        self,
        pref_config: dict[str, typing.Any],
        function: SessionCallback,
        before_user_flow: typing.Optional[typing.Callable[[], typing.Any]] = None
    ) -> None:
        """建立工具会话并执行回调。"""
        ...


class ClientToolProvider(object):
    """提供 Mind 内置客户端工具注册表。"""

    def __init__(self, mind: "Mind") -> None:
        """保存 Mind 上下文。"""
        self._mind = mind

    def registry(self) -> typing.Any:
        """返回当前工作区对应的客户端工具注册表。"""
        return self._mind.client_tools


class ExternalMcpProvider(object):
    """提供已启动的外部 MCP 工具分组。"""

    def __init__(self, mind: "Mind") -> None:
        """保存 Mind 上下文。"""
        self._mind = mind

    def group(self) -> typing.Any:
        """返回外部 MCP 分组，未启动时返回空。"""
        runtime = self._mind.external_mcp
        return runtime.group if runtime else None


class ServiceMcpProvider(object):
    """提供可选的 Helix MCP 服务会话。"""

    @staticmethod
    def open_session() -> typing.Any:
        """打开服务 MCP 会话上下文。"""
        return open_local_mcp_session()


class CompositeToolRuntime(object):
    """组合多个工具来源并提供统一会话。"""

    def __init__(self, mind: "Mind") -> None:
        """保存运行所需的共享上下文。"""
        self._mind = mind

        self.client_provider   = ClientToolProvider(mind)
        self.external_provider = ExternalMcpProvider(mind)
        self.service_provider  = ServiceMcpProvider()

    @staticmethod
    async def run_before_user_flow(
        before_user_flow: typing.Optional[typing.Callable[[], typing.Any]]
    ) -> None:
        """执行用户流程前置回调。"""
        if before_user_flow is None:
            return None

        callback_result = before_user_flow()
        if inspect.isawaitable(callback_result):
            await callback_result

    async def run_with_context(
        self,
        service_session: typing.Any,
        function: SessionCallback,
        before_user_flow: typing.Optional[typing.Callable[[], typing.Any]]
    ) -> None:
        """构建组合工具上下文并执行用户回调。"""
        tool_context = await build_tool_context(
            service_session,
            self.external_provider.group(),
            client_registry=self.client_provider.registry()
        )

        await self.run_before_user_flow(before_user_flow)

        await function(
            tool_context.session,
            tool_context.tools
        )

    async def with_session(
        self,
        pref_config: dict[str, typing.Any],
        function: SessionCallback,
        before_user_flow: typing.Optional[typing.Callable[[], typing.Any]] = None
    ) -> None:
        """建立工具会话并执行回调。"""
        _ = pref_config

        if not self._mind.is_service_mcp_linked():
            await self.run_with_context(
                None,
                function,
                before_user_flow
            )
            return None

        service_stack = contextlib.AsyncExitStack()

        try:
            service_session = await service_stack.enter_async_context(
                self.service_provider.open_session()
            )
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except Exception as exc:
            await service_stack.aclose()
            observe_exception(
                "tool_runtime.service_provider.failed",
                exc,
                level="WARNING",
            )
        else:
            async with service_stack:
                await self.run_with_context(
                    service_session,
                    function,
                    before_user_flow
                )
                return None

        await self.run_with_context(
            None,
            function,
            before_user_flow
        )


if __name__ == '__main__':
    pass
