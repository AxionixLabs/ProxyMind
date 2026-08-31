# -*- coding: utf-8 -*-

import typing

from protocol.schema.stream_events import StreamEvent
from protocol.schema.turn_inputs import TurnInput
from protocol.transport.events import EventReport
from .mcp_session import McpSessionPort

if typing.TYPE_CHECKING:
    from agent.application.run_result import RunResult
    from agent.application.turn_execution import TurnExecution


class SubagentExecutionPort(typing.Protocol):
    """定义执行已经准备好的子模型轮次所需能力。"""

    async def execute(
        self,
        pref_config: dict[str, typing.Any],
        skills: list[dict[str, str]],
        execution: "TurnExecution",
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport,
        on_turn_input_event: (
            typing.Callable[[StreamEvent], TurnInput | None] | None
        ) = None,
    ) -> "RunResult":
        """执行子模型轮次并返回结构化结果。"""
        ...


SubagentResultValue = typing.TypeVar(
    "SubagentResultValue",
    bound="RunResult",
    covariant=True,
)


class SubagentOperation(typing.Protocol[SubagentResultValue]):
    """定义使用固定 Hook 作用域执行子轮次的操作端口。"""

    async def __call__(
        self,
        execution: "TurnExecution",
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport,
    ) -> SubagentResultValue:
        """执行子轮次并返回稳定结果。"""
        ...


__all__ = (
    "SubagentExecutionPort",
    "SubagentOperation",
    "SubagentResultValue",
)
