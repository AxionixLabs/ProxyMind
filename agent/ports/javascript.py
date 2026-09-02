# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
import typing
from collections.abc import (
    Awaitable,
    Callable,
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


@dataclass(frozen=True, slots=True)
class JavaScriptExecutionRequest:
    """描述一次绑定 Session 与安全信封的 JavaScript Cell 请求。"""

    session_id: str
    code: str
    cwd: str
    access_mode: SandboxMode
    timeout_ms: int


class JavaScriptResetDisposition(enum.StrEnum):
    """描述重置命令是否关闭了已经启动的 Kernel。"""

    NOT_STARTED = "not_started"
    RESET = "reset"


class JavaScriptExecutionPort(typing.Protocol):
    """定义应用层调用持久 JavaScript Sidecar 的执行契约。"""

    agent_id: str

    async def execute(
        self,
        *,
        request: JavaScriptExecutionRequest,
        call_tool: NestedToolDispatch,
    ) -> JavaScriptExecution:
        """执行一个 Cell，并返回完成边界已校验的领域无关结果。"""
        ...

    async def reset_session(
        self,
        session_id: str,
    ) -> JavaScriptResetDisposition:
        """重置指定会话的 Kernel，并返回明确处置结果。"""
        ...


class JavaScriptSessionLifecyclePort(typing.Protocol):
    """定义 Harness Session 与进程生命周期使用的 Sidecar 清理端口。"""

    async def close_session(self, session_id: str) -> None:
        """幂等关闭指定 Harness Session 的 Kernel。"""
        ...

    async def close(self) -> None:
        """幂等关闭当前进程持有的全部 Kernel。"""
        ...


__all__ = (
    "JavaScriptExecution",
    "JavaScriptExecutionError",
    "JavaScriptExecutionPort",
    "JavaScriptExecutionRequest",
    "JavaScriptFailureKind",
    "JavaScriptResetDisposition",
    "JavaScriptSessionLifecyclePort",
    "NestedToolDispatch",
    "NestedToolOutput",
)

if __name__ == "__main__":
    pass
