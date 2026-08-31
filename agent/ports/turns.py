# -*- coding: utf-8 -*-

import typing

from protocol.schema.stream_events import StreamEvent
from protocol.schema.turn_inputs import TurnInput
from protocol.transport.events import EventReport
from .mcp_session import McpSessionPort

if typing.TYPE_CHECKING:
    from agent.application.turns.execution import TurnExecution


class TurnResultPort(typing.Protocol):
    """定义模型轮次结果必须提供的稳定状态。"""

    @property
    def status(self) -> str:
        """返回模型轮次的稳定结束状态。"""
        ...


TurnResultValue = typing.TypeVar(
    "TurnResultValue",
    bound=TurnResultPort,
    covariant=True,
)


class TurnOperation(typing.Protocol[TurnResultValue]):
    """定义在工具会话中执行单个模型轮次的操作端口。"""

    async def __call__(
        self,
        execution: "TurnExecution",
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport,
    ) -> TurnResultValue:
        """执行模型轮次并返回稳定结果。"""
        ...


class TurnInputEventHandler(typing.Protocol):
    """定义模型流向子 Agent mailbox 转发输入事件的回调端口。"""

    def __call__(self, event: StreamEvent) -> TurnInput | None:
        """处理一个流式输入事件并返回可确认的 TurnInput。"""
        ...


__all__ = (
    "TurnInputEventHandler",
    "TurnOperation",
    "TurnResultPort",
    "TurnResultValue",
)
