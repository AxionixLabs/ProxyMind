# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from agent.protocol import (
    CanonicalItem,
    McpToolDefinition,
    McpToolResult,
    ModelEvent,
    ModelStreamEndReason,
    ModelStreamRequest,
    SubmitTurnCommand,
)
from agent.protocol.json_value import (
    JsonValue,
    ThawedJsonValue,
    freeze_json,
    thaw_object,
)

ReconnectStatusCallback: typing.TypeAlias = Callable[[bool], None]

HelixState: typing.TypeAlias = typing.Literal[
    "stopped",
    "starting",
    "ready",
    "restarting",
    "failed",
    "closed",
]

SandboxMode: typing.TypeAlias = typing.Literal[
    "danger-full-access",
    "read-only",
    "workspace-read",
    "workspace-write",
]

SandboxPermission: typing.TypeAlias = typing.Literal[
    "use_default",
    "require_escalated",
    "with_additional_permissions",
]


class CapabilityError(RuntimeError):
    """表示模型、MCP、Helix、进程或文件能力已经归一化的失败。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """校验并保存跨能力边界的稳定错误快照。"""
        normalized_code = str(code or "").strip()
        normalized_message = str(message or "").strip()
        if not normalized_code:
            raise ValueError("capability error code is required")
        if not normalized_message:
            raise ValueError("capability error message is required")
        if not isinstance(retryable, bool):
            raise TypeError("capability error retryable must be boolean")
        raw_details: Mapping[str, JsonValue] = details or {}
        if not isinstance(raw_details, Mapping):
            raise TypeError("capability error details must be an object")
        frozen_details = freeze_json(
            dict(raw_details),
            field_name="capability error details",
        )
        if not isinstance(frozen_details, Mapping):
            raise TypeError("capability error details must be an object")

        self.code = normalized_code
        self.retryable = retryable
        self._details = frozen_details
        super().__init__(normalized_message)

    @property
    def message(self) -> str:
        """返回稳定的错误消息。"""
        return str(self)

    @property
    def details(self) -> dict[str, ThawedJsonValue]:
        """返回错误细节的独立副本。"""
        return thaw_object(self._details, field_name="capability error details")

    def to_dict(self) -> dict[str, ThawedJsonValue]:
        """返回可写入事件或日志的错误快照。"""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """描述受控进程能力的一次启动请求。"""

    argv: tuple[str, ...]
    cwd: str | Path
    env: Mapping[str, str] = field(default_factory=dict)
    sandbox_mode: SandboxMode = "danger-full-access"
    sandbox_permissions: SandboxPermission = "use_default"
    additional_permissions: Mapping[str, JsonValue] | None = None
    stdin_open: bool = True

    def __post_init__(self) -> None:
        """校验进程参数并冻结环境快照。"""
        if isinstance(self.argv, (str, bytes)):
            raise TypeError("process argv must be a sequence of strings")
        argv = tuple(self.argv)
        if not argv or not isinstance(argv[0], str) or not argv[0].strip():
            raise ValueError("process argv is required")
        if any(not isinstance(item, str) or not item for item in argv):
            raise TypeError("process argv must contain only strings")
        cwd = str(self.cwd or "").strip()
        if not cwd:
            raise ValueError("process cwd is required")
        if self.sandbox_mode not in {
            "danger-full-access",
            "read-only",
            "workspace-read",
            "workspace-write",
        }:
            raise ValueError("process sandbox_mode is invalid")
        if self.sandbox_permissions not in {
            "use_default",
            "require_escalated",
            "with_additional_permissions",
        }:
            raise ValueError("process sandbox_permissions is invalid")
        if self.sandbox_permissions == "with_additional_permissions":
            if not isinstance(self.additional_permissions, Mapping):
                raise ValueError(
                    "process additional_permissions are required for the selected permission"
                )
        elif self.additional_permissions is not None:
            raise ValueError(
                "process additional_permissions require with_additional_permissions"
            )
        if not isinstance(self.stdin_open, bool):
            raise TypeError("process stdin_open must be boolean")
        if not isinstance(self.env, Mapping):
            raise TypeError("process env must be an object")
        env: dict[str, str] = {}
        for key, value in self.env.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("process env contains an empty name")
            if not isinstance(value, str):
                raise TypeError("process env values must be strings")
            env[key.strip()] = value
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "env", MappingProxyType(env))
        if self.additional_permissions is not None:
            additional_permissions = freeze_json(
                dict(self.additional_permissions),
                field_name="process additional_permissions",
            )
            if not isinstance(additional_permissions, Mapping):
                raise TypeError("process additional_permissions must be an object")
            object.__setattr__(
                self,
                "additional_permissions",
                additional_permissions,
            )


@typing.runtime_checkable
class McpCapability(typing.Protocol):
    """提供 MCP 工具发现、调用和关闭生命周期。"""

    async def list_tools(self) -> tuple[McpToolDefinition, ...]:
        """返回当前可用的工具定义快照。"""
        ...

    async def call_tool(
        self,
        name: str,
        *,
        arguments: Mapping[str, JsonValue] | None = None,
        call_id: str | None = None,
    ) -> McpToolResult:
        """调用一个已发现工具并返回稳定结果。"""
        ...

    async def aclose(self) -> None:
        """关闭能力持有的会话和连接。"""
        ...


@typing.runtime_checkable
class HelixCapability(typing.Protocol):
    """提供 Helix 执行面的串行启停和就绪生命周期。"""

    @property
    def state(self) -> HelixState:
        """返回当前生命周期状态。"""
        ...

    async def ensure_ready(self, *, wait_sec: float = 10.0) -> None:
        """确保执行面就绪。"""
        ...

    async def restart(self, *, wait_sec: float = 10.0) -> None:
        """重启执行面并等待再次就绪。"""
        ...

    async def stop(self) -> None:
        """停止执行面但保留能力对象。"""
        ...

    async def aclose(self) -> None:
        """关闭执行面并释放所有资源。"""
        ...


@typing.runtime_checkable
class ProcessHandle(typing.Protocol):
    """提供受控进程的读写、等待和终止生命周期。"""

    session_id: str
    pid: int | None
    returncode: int | None

    async def read_stdout(self) -> AsyncIterator[str]:
        """按顺序读取标准输出文本片段。"""
        ...

    async def read_stderr(self) -> AsyncIterator[str]:
        """按顺序读取标准错误文本片段。"""
        ...

    async def write(self, data: str, *, eof: bool = False) -> None:
        """向标准输入写入文本或发送 EOF。"""
        ...

    async def wait(self) -> int:
        """等待进程退出并返回退出码。"""
        ...

    async def terminate(self, *, force: bool = False) -> None:
        """请求进程优雅退出或强制终止。"""
        ...

    async def aclose(self) -> None:
        """关闭句柄并回收底层进程。"""
        ...


@typing.runtime_checkable
class ProcessCapability(typing.Protocol):
    """提供受控进程的创建和全局回收能力。"""

    async def spawn(self, spec: ProcessSpec) -> ProcessHandle:
        """按不可变启动参数创建一个进程句柄。"""
        ...

    async def aclose(self) -> None:
        """回收该能力创建的全部进程。"""
        ...


@typing.runtime_checkable
class FilesystemCapability(typing.Protocol):
    """提供限定根目录内的受控文件读写能力。"""

    async def read_text(self, path: str) -> str:
        """读取根目录内的 UTF-8 文本文件。"""
        ...

    async def write_text(self, path: str, content: str) -> None:
        """写入根目录内的 UTF-8 文本文件。"""
        ...

    async def exists(self, path: str) -> bool:
        """判断根目录内的路径是否存在。"""
        ...

    async def list_files(self, path: str = ".") -> tuple[str, ...]:
        """列出根目录内指定目录的直接文件项。"""
        ...

    async def aclose(self) -> None:
        """释放文件能力持有的资源。"""
        ...


@typing.runtime_checkable
class EnvironmentSnapshotCapability(typing.Protocol):
    """为新 Turn 捕获经正式协议校验的客户端环境事实。

    实现方可以缓存进程级静态探测结果，但每次捕获必须返回独立快照；application
    会把它冻结进提交命令，后续排队、恢复和执行不得重新读取本机环境。
    """

    def capture(
        self,
        *,
        cwd: str | Path,
        workspace_root: str | Path,
        providers: Mapping[str, Mapping[str, JsonValue]] | None = None,
    ) -> Mapping[str, JsonValue]:
        """返回一个新 Turn 的完整环境快照。"""
        ...

    def clear_cache(self) -> None:
        """清除实现持有的可复用环境探测事实。"""
        ...

ApprovalSnapshotCallback: typing.TypeAlias = Callable[
    [object],
    Awaitable[None] | None,
]


class ModelCapabilityError(CapabilityError):
    """表示模型能力边界已经将传输或协议失败归一化。

    capability adapter 负责创建此异常并提供稳定错误码；runtime 只读取公开字段，
    将其写入本轮终态和持久事件，不得依赖具体 HTTP 客户端异常类型。
    """

    pass


@typing.runtime_checkable
class ModelEventStream(typing.Protocol):
    """暴露模型事件、Canonical Item 投影、游标和关闭生命周期。"""

    end_reason: ModelStreamEndReason | None
    last_event_seq: int

    @property
    def canonical_items(self) -> tuple[CanonicalItem, ...]:
        """返回当前未被展示替换的 Item 快照。"""
        ...

    @property
    def canonical_item_history(self) -> tuple[CanonicalItem, ...]:
        """返回包含旧展示版本的 Item 审计快照。"""
        ...

    @property
    def pending_approval_items(self) -> tuple[CanonicalItem, ...]:
        """返回快照对账后仍等待客户端决定的审批 Item。"""
        ...

    @property
    def assistant_text(self) -> str:
        """返回当前未被展示替换的 canonical 正文。"""
        ...

    def __aiter__(self) -> AsyncIterator[ModelEvent]:
        """返回满足模型事件坐标契约的异步迭代器。"""
        ...

    async def aclose(self) -> None:
        """关闭当前传输及其重连资源。"""
        ...


@typing.runtime_checkable
class ModelCapability(typing.Protocol):
    """按冻结请求创建模型事件流并持有协议 Session 恢复状态。

    实现方不得持有 Harness Run 或前端组件状态；跨 Turn 事件水位和协议投影属于
    Protocol Client，可在同一进程的多个入口之间共享。
    """

    def stream(
        self,
        request: ModelStreamRequest,
        *,
        on_reconnect_status: ReconnectStatusCallback | None = None,
        on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    ) -> ModelEventStream:
        """创建可取消、可关闭且可报告服务端事件游标的流。"""
        ...


class TurnExecutorResult(typing.Protocol):
    """约束主动 Turn 执行结果必须提供稳定状态和协议字典。"""

    status: str

    def to_dict(self) -> dict[str, typing.Any]:
        """返回不包含运行时对象的结构化结果。"""
        ...


TurnResultValue = typing.TypeVar(
    "TurnResultValue",
    bound=TurnExecutorResult,
    covariant=True,
)


class TurnExecutor(typing.Protocol[TurnResultValue]):
    """执行一个已固定身份的主动 Turn，不持有 Session 状态。"""

    async def __call__(
        self,
        command: SubmitTurnCommand,
    ) -> TurnResultValue:
        """执行命令并返回具有稳定 status 的结果。"""
        ...


if __name__ == '__main__':
    pass
