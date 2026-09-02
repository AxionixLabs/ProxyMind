# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

import enum
import typing
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import dataclass

from agent.ports.capabilities import SandboxMode
from agent.protocol.json_value import ThawedJsonValue

NestedToolOutput: typing.TypeAlias = dict[str, ThawedJsonValue]
NestedToolDispatch: typing.TypeAlias = Callable[
    [str, dict[str, typing.Any], str],
    Awaitable[NestedToolOutput],
]


class JavaScriptFailureKind(enum.StrEnum):
    """定义 JavaScript 执行边界的稳定失败类别。"""

    UNAVAILABLE = "unavailable"
    PROTOCOL_ERROR = "protocol_error"
    EXECUTION_TIMEOUT = "execution_timeout"
    CANCELLED = "cancelled"
    RUNTIME_ERROR = "runtime_error"


class JavaScriptExecutionError(RuntimeError):
    """携带 JavaScript 执行失败类别和有界诊断。"""

    def __init__(
        self,
        kind: JavaScriptFailureKind,
        detail: str,
    ) -> None:
        """保存可供调用边界映射的稳定失败事实。"""
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class JavaScriptExecution:
    """描述一次 JavaScript Cell 的已校验执行结果。"""

    output: str
    attachments: tuple[dict[str, ThawedJsonValue], ...] = ()


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
    "JavaScriptExecution",
    "JavaScriptExecutionError",
    "JavaScriptFailureKind",
    "NestedToolDispatch",
    "NestedToolOutput",
    "WorkspaceJavaScriptPort",
)

if __name__ == "__main__":
    pass
