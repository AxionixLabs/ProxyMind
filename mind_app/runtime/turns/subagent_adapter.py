# -*- coding: utf-8 -*-

import typing

from agent.application.turns.execution import TurnExecution
from agent.application.turns.run_result import RunResult
from agent.application.tools.execution import ToolExecutionAdapter
from agent.ports import (
    EffectJournalFactory,
    McpSessionPort,
    ModelCapability,
    ProtocolCommandClient,
    SubagentExecutionPort,
    SubagentOperation,
    TurnExecutionRuntimePort,
    TurnInputEventHandler,
    OutputSessionFactory,
)
from mind_app.runtime.turns.executor import execute_turn
from mind_app.runtime.turns.stream import stream_turn
from protocol.transport.events import EventReport


class ControllerSubagentTurnRunner:
    """把 Harness 子轮次执行适配到当前 Turn 执行器。"""

    def __init__(self, runtime: TurnExecutionRuntimePort) -> None:
        """绑定组合根提供的 Turn 执行运行时。"""
        self._runtime = runtime

    async def __call__(
        self,
        pref_config: dict[str, typing.Any],
        execution: TurnExecution,
        operation: SubagentOperation[RunResult],
        *,
        event_report: EventReport | None = None,
    ) -> RunResult:
        """使用固定运行时执行一次子 Agent Turn。"""
        return await execute_turn(
            self._runtime,
            pref_config,
            execution,
            operation,
            event_report=event_report,
        )


class ControllerSubagentExecution(SubagentExecutionPort):
    """把流式模型能力适配为 Harness 子 Agent 执行端口。"""

    def __init__(
        self,
        runtime: TurnExecutionRuntimePort,
        *,
        model_capability: ModelCapability | None,
        protocol_client: ProtocolCommandClient | None,
        effect_journal_factory: EffectJournalFactory | None,
        tool_execution: ToolExecutionAdapter,
        session_factory: OutputSessionFactory,
    ) -> None:
        """绑定模型、协议和效果账本能力。"""
        self._runtime = runtime
        self._model_capability = model_capability
        self._protocol_client = protocol_client
        self._effect_journal_factory = effect_journal_factory
        self._tool_execution = tool_execution
        self._session_factory = session_factory

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
        """使用静默输出会话执行一次子 Agent 流。"""
        return await stream_turn(
            self._runtime,
            session=session,
            pref_config=pref_config,
            tools=tools,
            turn_execution=execution,
            model_capability=self._model_capability,
            protocol_client=self._protocol_client,
            effect_journal_factory=self._effect_journal_factory,
            tool_execution=self._tool_execution,
            ev_report=event_report,
            skills=skills,
            session_factory=self._session_factory,
            on_turn_input_event=on_turn_input_event,
        )


__all__ = (
    "ControllerSubagentExecution",
    "ControllerSubagentTurnRunner",
)
