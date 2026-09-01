# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.turns.run_result import RunResult
from agent.application.turns.execution import TurnExecution
from protocol.transport.events import EventReport
from agent.ports import (
    McpSessionPort,
    SubagentStreamPort,
    TurnInputEventHandler,
)

__all__ = ("StreamSubagentExecution",)


class StreamSubagentExecution:
    """通过注入的流式端口执行无前台输出的子轮次。"""

    def __init__(self, stream_runner: SubagentStreamPort) -> None:
        """绑定具体流式执行端口，不持有 Controller 或输出实现。"""
        self._stream_runner = stream_runner

    async def execute(
        self,
        pref_config: dict[str, typing.Any],
        skills: list[dict[str, str]],
        execution: TurnExecution,
        session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        event_report: EventReport,
        on_turn_input_event: TurnInputEventHandler | None = None,
    ) -> RunResult:
        """使用独立静默输出会话执行固定子轮次。"""
        if execution.context.agent.depth == 0:
            raise ValueError("subagent execution requires a child agent context")

        return await self._stream_runner(
            session=session,
            turn_execution=execution,
            pref_config=pref_config,
            tools=tools,
            event_report=event_report,
            skills=skills,
            on_turn_input_event=on_turn_input_event,
        )


if __name__ == '__main__':
    pass
