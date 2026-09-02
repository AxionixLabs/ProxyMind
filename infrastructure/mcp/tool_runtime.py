# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import inspect
import typing

from agent.ports import (
    BeforeToolSession,
    McpSessionPort,
    ToolRuntimePort,
    ToolRuntimeSources,
    ToolSessionCallback,
    ToolSessionFactory,
)
from infrastructure.mcp.local_session import open_local_mcp_session
from infrastructure.mcp.tool_catalog import build_tool_context
from observability import observe_exception

SessionResult = typing.TypeVar("SessionResult")


class CompositeToolRuntime(ToolRuntimePort):
    """在一次 Turn 内组合本地、外部和服务 MCP 工具来源。"""

    def __init__(
        self,
        sources: ToolRuntimeSources,
        *,
        service_session_factory: ToolSessionFactory = open_local_mcp_session,
    ) -> None:
        """绑定动态工具来源和可选服务 MCP 会话工厂。"""
        if not isinstance(sources, ToolRuntimeSources):
            raise TypeError("tool runtime sources are required")
        if not callable(service_session_factory):
            raise TypeError("service MCP session factory must be callable")
        self._sources = sources
        self._open_service_session = service_session_factory

    @staticmethod
    async def run_before_user_flow(
        before_user_flow: BeforeToolSession | None,
    ) -> None:
        """执行用户流程前置回调。"""
        if before_user_flow is None:
            return None

        callback_result = before_user_flow()
        if inspect.isawaitable(callback_result):
            await callback_result

    async def run_with_context(
        self,
        service_session: McpSessionPort | None,
        function: ToolSessionCallback[SessionResult],
        before_user_flow: BeforeToolSession | None,
    ) -> SessionResult:
        """冻结当前工具来源，构建组合会话并执行调用方用例。"""
        tool_context = await build_tool_context(
            service_session=service_session,
            external_group=self._sources.external_group(),
            client_registry=self._sources.client_registry(),
            builtin_registry=self._sources.builtin_registry(),
        )

        await self.run_before_user_flow(before_user_flow)

        return await function(
            tool_context.session,
            tool_context.tools,
        )

    async def with_session(
        self,
        pref_config: dict[str, typing.Any],
        function: ToolSessionCallback[SessionResult],
        before_user_flow: BeforeToolSession | None = None,
    ) -> SessionResult:
        """按服务挂载状态建立组合工具会话并执行调用方用例。"""
        _ = pref_config

        if not self._sources.service_linked():
            return await self.run_with_context(
                None,
                function,
                before_user_flow,
            )

        service_stack = contextlib.AsyncExitStack()

        try:
            service_session = await service_stack.enter_async_context(
                self._open_service_session()
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
                return await self.run_with_context(
                    service_session,
                    function,
                    before_user_flow,
                )

        return await self.run_with_context(
            None,
            function,
            before_user_flow,
        )


if __name__ == '__main__':
    pass
