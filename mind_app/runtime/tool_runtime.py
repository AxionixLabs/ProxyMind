# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import inspect
from mind_app.mcp import (
    McpSessionLike,
    build_tool_context
)
from mind_nova import const
from .local_mcp import open_local_mcp_session
from .session_policy import bootstrap_failure

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


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


class HelixToolRuntime(object):
    """通过本地服务提供工具会话。"""

    def __init__(self, mind: "Mind") -> None:
        """保存运行所需的共享上下文。"""
        self._mind = mind

    def external_group(self) -> typing.Any:
        """返回已连接的外部工具分组。"""
        runtime = self._mind.external_mcp
        return runtime.group if runtime else None

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

    async def with_session(
        self,
        pref_config: dict[str, typing.Any],
        function: SessionCallback,
        before_user_flow: typing.Optional[typing.Callable[[], typing.Any]] = None
    ) -> None:
        """建立工具会话并执行回调。"""
        _ = pref_config

        mcp_url        = const.BASE_URL + const.MCP_ED
        bootstrap_done = False

        try:
            async with open_local_mcp_session() as local_session:
                tool_context = await build_tool_context(
                    local_session,
                    self.external_group()
                )
                bootstrap_done = True

                await self.run_before_user_flow(before_user_flow)

                await function(
                    tool_context.session,
                    tool_context.tools
                )

        except BaseException as exc:
            if bootstrap_done:
                raise

            raise bootstrap_failure(exc, mcp_url=mcp_url) from None


if __name__ == '__main__':
    pass
