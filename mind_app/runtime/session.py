# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import inspect
from mind_app.mcp import (
    build_tool_context,
    McpSessionLike,
)
from mind_nova import const
from .local_mcp import open_local_mcp_session
from .session_policy import bootstrap_failure

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


async def with_mcp_session(
    mind: "Mind",
    pref_config: dict[str, typing.Any],
    function: typing.Callable[
        [
            McpSessionLike,
            list[dict[str, typing.Any]],
        ],
        typing.Awaitable[None]
    ],
    before_user_flow: typing.Optional[typing.Callable[[], typing.Any]] = None
) -> None:
    """建立共享 MCP 会话，并把工具信息注入到调用流程。"""
    _ = pref_config

    mcp_url        = const.BASE_URL + const.MCP_ED
    bootstrap_done = False

    try:
        async with open_local_mcp_session() as local_session:

            external_group = mind.external_mcp.group if mind.external_mcp else None
            tool_context   = await build_tool_context(local_session, external_group)
            bootstrap_done = True

            if before_user_flow is not None:
                callback_result = before_user_flow()
                if inspect.isawaitable(callback_result):
                    await callback_result

            await function(
                tool_context.session,
                tool_context.tools,
            )

    except BaseException as exc:
        if bootstrap_done:
            raise

        raise bootstrap_failure(exc, mcp_url=mcp_url) from None


if __name__ == '__main__':
    pass
