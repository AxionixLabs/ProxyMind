# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from protocol.transport.events import EventReport
from protocol.schema.stream_events import StreamEvent
from protocol.schema.turn_inputs import TurnInput
from mind_app.runtime.mcp.contracts import McpSessionLike
from mind_app.runtime.turns.result import RunResult
from mind_app.presentation.output.silent import create_silent_output_session
from mind_app.runtime.turns.executor import TurnExecution

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


class SubagentExecutionPort(typing.Protocol):
    """定义执行已经准备好的子模型轮次所需能力。"""

    async def execute(
        self,
        pref_config: dict[str, typing.Any],
        skills: list[dict[str, str]],
        execution: TurnExecution,
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport,
        on_turn_input_event: (
            typing.Callable[[StreamEvent], TurnInput | None] | None
        ) = None,
    ) -> RunResult:
        """执行子模型轮次并返回结构化结果。"""
        ...


class StreamSubagentExecutor:
    """通过现有流式模式执行器运行无前台输出的子轮次。"""

    def __init__(self, controller: "Mind") -> None:
        self._controller = controller

    async def execute(
        self,
        pref_config: dict[str, typing.Any],
        skills: list[dict[str, str]],
        execution: TurnExecution,
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport,
        on_turn_input_event: (
            typing.Callable[[StreamEvent], TurnInput | None] | None
        ) = None,
    ) -> RunResult:
        """使用独立静默输出会话执行固定子轮次。"""
        if execution.context.agent.depth == 0:
            raise ValueError("subagent execution requires a child agent context")

        from mind_app.runtime.turns.stream import stream_turn

        return await stream_turn(
            self._controller,
            session=session,
            pref_config=pref_config,
            tools=tools,
            turn_execution=execution,
            ev_report=event_report,
            skills=skills,
            session_factory=create_silent_output_session,
            on_turn_input_event=on_turn_input_event,
        )


if __name__ == '__main__':
    pass
