# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import typing
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)

from agent.ports.capabilities import SandboxMode
from agent.protocol.json_value import ThawedJsonValue

NestedToolOutput: typing.TypeAlias = dict[str, ThawedJsonValue]
NestedToolDispatch: typing.TypeAlias = Callable[
    [str, dict[str, typing.Any], str],
    Awaitable[NestedToolOutput],
]


class WorkspaceJavaScriptPort(typing.Protocol):
    """定义绑定工作区的持久 JavaScript 内核执行契约。"""

    agent_id: str

    async def js_repl(
        self,
        *,
        session_id: str,
        code: str,
        cwd: str,
        access_mode: SandboxMode,
        timeout_ms: int,
        call_tool: NestedToolDispatch,
    ) -> Mapping[str, typing.Any]:
        """执行一个 Cell，并允许通过稳定 JSON 回调调用嵌套工具。"""
        ...

    async def reset_js_repl(
        self,
        session_id: str,
    ) -> Mapping[str, typing.Any]:
        """重置指定会话持有的内核。"""
        ...


__all__ = (
    "NestedToolDispatch",
    "NestedToolOutput",
    "WorkspaceJavaScriptPort",
)

if __name__ == "__main__":
    pass
