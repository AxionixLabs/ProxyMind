# -*- coding: utf-8 -*-

import typing

from agent.adapters.protocol.turn_stream import stream_turn
from agent.application.tools.execution import ToolExecutionAdapter
from agent.application.turns.execution import TurnExecution
from agent.application.turns.run_result import RunResult
from agent.ports import (
    EffectJournalFactory,
    EventReportPort,
    McpSessionPort,
    ModelCapability,
    OutputSessionFactory,
    ProtocolCommandClient,
    TurnInputEventHandler,
)


class ProtocolSubagentStream:
    """绑定协议与输出能力并执行无前台生命周期的子 Agent 流。"""

    def __init__(
        self,
        *,
        model_capability: ModelCapability,
        protocol_client: ProtocolCommandClient,
        effect_journal_factory: EffectJournalFactory,
        tool_execution: ToolExecutionAdapter,
        session_factory: OutputSessionFactory,
    ) -> None:
        """绑定进程级模型、命令、效果、工具和输出能力。"""
        if not isinstance(model_capability, ModelCapability):
            raise TypeError("subagent model capability is invalid")
        if not isinstance(protocol_client, ProtocolCommandClient):
            raise TypeError("subagent protocol client is invalid")
        if not callable(effect_journal_factory):
            raise TypeError("subagent effect journal factory is invalid")
        if not isinstance(tool_execution, ToolExecutionAdapter):
            raise TypeError("subagent tool execution adapter is invalid")
        if not callable(session_factory):
            raise TypeError("subagent output session factory is invalid")
        self._model_capability = model_capability
        self._protocol_client = protocol_client
        self._effect_journal_factory = effect_journal_factory
        self._tool_execution = tool_execution
        self._session_factory = session_factory

    async def __call__(
        self,
        session: McpSessionPort,
        pref_config: dict[str, typing.Any],
        tools: list[dict[str, typing.Any]],
        *,
        turn_execution: TurnExecution,
        event_report: EventReportPort,
        skills: list[dict[str, str]],
        on_turn_input_event: TurnInputEventHandler | None = None,
    ) -> RunResult:
        """使用绑定能力执行一次子 Agent 协议流。"""
        return await stream_turn(
            session,
            pref_config,
            tools,
            turn_execution=turn_execution,
            model_capability=self._model_capability,
            protocol_client=self._protocol_client,
            effect_journal_factory=self._effect_journal_factory,
            tool_execution=self._tool_execution,
            session_factory=self._session_factory,
            ev_report=event_report,
            skills=skills,
            on_turn_input_event=on_turn_input_event,
        )


__all__ = ("ProtocolSubagentStream",)
