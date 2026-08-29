# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import AsyncIterator, Awaitable, Callable
from agent.protocol import (
    ModelStreamEndReason,
    ModelStreamRequest,
    SubmitTurnCommand,
)


ReconnectStatusCallback: typing.TypeAlias = Callable[[bool], None]
ApprovalSnapshotCallback: typing.TypeAlias = Callable[
    [object],
    Awaitable[None] | None,
]


class ModelEventStream(typing.Protocol):
    """暴露模型事件迭代、恢复游标和显式关闭生命周期。"""

    end_reason: ModelStreamEndReason | None
    last_event_seq: int

    def __aiter__(self) -> AsyncIterator[typing.Any]:
        """返回类型化模型事件的异步迭代器。"""
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
