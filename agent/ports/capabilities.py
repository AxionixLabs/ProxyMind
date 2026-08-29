# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable
)
from agent.protocol import (
    ModelEvent,
    ModelStreamEndReason,
    ModelStreamRequest,
    SubmitTurnCommand,
)
from agent.protocol.json_value import (
    JsonValue,
    freeze_json,
    thaw_json,
)

ReconnectStatusCallback: typing.TypeAlias = Callable[[bool], None]

ApprovalSnapshotCallback: typing.TypeAlias = Callable[
    [object],
    Awaitable[None] | None,
]


class ModelCapabilityError(RuntimeError):
    """表示模型能力边界已经将传输或协议失败归一化。

    capability adapter 负责创建此异常并提供稳定错误码；runtime 只读取公开字段，
    将其写入本轮终态和持久事件，不得依赖具体 HTTP 客户端异常类型。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: typing.Mapping[str, JsonValue] | None = None,
    ) -> None:
        """校验并保存可序列化的模型能力错误快照。"""
        normalized_code = str(code or "").strip()
        if not normalized_code:
            raise ValueError("model capability error code is required")
        normalized_message = str(message or "").strip()
        if not normalized_message:
            raise ValueError("model capability error message is required")
        if not isinstance(retryable, bool):
            raise TypeError("model capability error retryable must be boolean")
        if details is None:
            details = {}
        if not isinstance(details, typing.Mapping):
            raise TypeError("model capability error details must be an object")
        frozen_details = freeze_json(
            dict(details),
            field_name="model capability error details",
        )
        if not isinstance(frozen_details, typing.Mapping):
            raise TypeError("model capability error details must be an object")

        self.code = normalized_code
        self.retryable = retryable
        self._details = frozen_details
        super().__init__(normalized_message)

    @property
    def message(self) -> str:
        """返回已经清洗的稳定错误消息。"""
        return str(self)

    @property
    def details(self) -> dict[str, typing.Any]:
        """返回错误细节的独立可变副本。"""
        return typing.cast(dict[str, typing.Any], thaw_json(self._details))

    def to_dict(self) -> dict[str, typing.Any]:
        """返回可写入终态事件的错误对象。"""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


@typing.runtime_checkable
class ModelEventStream(typing.Protocol):
    """暴露模型事件迭代、恢复游标和显式关闭生命周期。"""

    end_reason: ModelStreamEndReason | None
    last_event_seq: int

    def __aiter__(self) -> AsyncIterator[ModelEvent]:
        """返回满足模型事件坐标契约的异步迭代器。"""
        ...

    async def aclose(self) -> None:
        """关闭当前传输及其重连资源。"""
        ...


@typing.runtime_checkable
class ModelCapability(typing.Protocol):
    """按冻结请求创建模型事件流，不持有 Session 或前端状态。"""

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
