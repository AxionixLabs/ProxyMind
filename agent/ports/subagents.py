# -*- coding: utf-8 -*-

import typing
from collections.abc import Awaitable, Callable

from protocol.schema.stream_events import StreamEvent
from protocol.schema.turn_inputs import TurnInput
from protocol.transport.events import EventReport
from .mcp_session import McpSessionPort
from .turns import TurnInputEventHandler

SkillsProvider: typing.TypeAlias = Callable[[], list[dict[str, str]]]

if typing.TYPE_CHECKING:
    from agent.application.turns.run_result import RunResult
    from agent.application.turns.execution import TurnExecution


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


class SubagentStreamPort(typing.Protocol):
    """定义流式模型适配器执行子 Agent Turn 的端口。"""

    async def __call__(
        self,
        session: McpSessionPort,
        pref_config: dict[str, typing.Any],
        tools: list[dict[str, typing.Any]],
        *,
        turn_execution: "TurnExecution",
        event_report: EventReport,
        skills: list[dict[str, str]],
        on_turn_input_event: TurnInputEventHandler | None = None,
    ) -> "RunResult":
        """执行流式子轮次并返回结构化结果。"""
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


class SubagentTurnRunner(typing.Protocol):
    """定义 Harness 调用一次子 Agent Turn 的端口。"""

    async def __call__(
        self,
        pref_config: dict[str, typing.Any],
        execution: "TurnExecution",
        operation: SubagentOperation["RunResult"],
        *,
        event_report: EventReport | None = None,
    ) -> "RunResult":
        """运行固定子轮次并返回结果。"""
        ...


class SubagentCleanupPort(typing.Protocol):
    """定义 Harness 等待异步清理收束的端口。"""

    async def await_cleanup(self, awaitable: Awaitable[None]) -> None:
        """等待清理协程完成。"""
        ...


__all__ = (
    "SkillsProvider",
    "SubagentExecutionPort",
    "SubagentStreamPort",
    "SubagentOperation",
    "SubagentResultValue",
    "SubagentTurnRunner",
    "SubagentCleanupPort",
)
