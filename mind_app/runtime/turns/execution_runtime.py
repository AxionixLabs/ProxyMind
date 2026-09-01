# -*- coding: utf-8 -*-

import typing
from collections.abc import Awaitable, Callable

from agent.ports import (
    McpSessionPort,
    TurnEventReportingPort,
    TurnExecutionRuntimePort,
)

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


class ControllerTurnExecutionRuntime(TurnExecutionRuntimePort):
    """把 Controller 的模型会话能力适配为执行器运行时端口。"""

    def __init__(self, controller: "Mind") -> None:
        """绑定组合根提供的执行器运行时边界。"""
        self._controller = controller

    @property
    def event_reporting(self) -> TurnEventReportingPort:
        """返回事件报告租约管理器。"""
        return self._controller.event_reporting

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        callback: Callable[
            [McpSessionPort, list[dict[str, typing.Any]]],
            Awaitable[typing.Any],
        ],
    ) -> typing.Any:
        """在 Controller 管理的 MCP 会话中运行执行器回调。"""
        return await self._controller.with_mcp_session(pref_config, callback)

    def tool_profile_for_turn(self) -> str | None:
        """返回当前 Controller 绑定的工具过滤档位。"""
        return self._controller.tool_profile_for_turn()

    async def await_cleanup(
        self,
        awaitable: Awaitable[typing.Any],
    ) -> typing.Any:
        """等待 Controller 提供的异步资源清理。"""
        return await self._controller.await_cleanup(awaitable)


__all__ = ("ControllerTurnExecutionRuntime",)
