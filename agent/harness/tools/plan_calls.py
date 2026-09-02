# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.hooks.models import (
    ToolOperationResult,
    ToolResultSnapshot
)
from agent.application.tools.execution import ToolExecutionAdapter
from agent.application.tools.planning import PLAN_STEPS_TOOL
from agent.application.turns.context import (
    ToolInvocation,
    TurnContext
)
from agent.application.views.builders.plan import build_plan_steps_start_view
from agent.application.views.contracts import PresentationSink
from agent.application.views.tool_execution import show_tool_result
from agent.harness.hooks.tool_lifecycle import ToolCallCoordinator
from agent.ports import McpSessionPort
from agent.ports import OutputControlPort
from .client_calls import ClientToolCallResult
from .plan_execution import (
    PlanExecutionReport,
    StepPlanExecutor
)


class PlanToolCallRunner:
    """编排 plan_steps 的展示和执行。"""

    def __init__(
        self,
        *,
        session: McpSessionPort,
        output_control: OutputControlPort,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        turn_context: TurnContext,
        pref_config: typing.Mapping[str, typing.Any],
        tool_call_coordinator: ToolCallCoordinator,
        tool_execution: ToolExecutionAdapter,
    ) -> None:
        """绑定计划执行所需端口和步骤执行器。"""
        self.output_control = output_control
        self.presentation = presentation

        self.executor = StepPlanExecutor(
            session=session,
            tools=tools,
            turn_context=turn_context,
            pref_config=pref_config,
            tool_call_coordinator=tool_call_coordinator,
            tool_execution=tool_execution,
        )

    async def handle(
        self,
        *,
        invocation: ToolInvocation
    ) -> "PlanExecutionReport":
        """处理一次完整的 plan_steps 工具调用。"""
        arguments = dict(invocation.arguments)
        call_id = invocation.call_id

        self.output_control.record_tool_arguments(
            PLAN_STEPS_TOOL,
            arguments,
            call_id=call_id,
        )

        await self.presentation.emit(
            build_plan_steps_start_view(arguments)
        )

        report = await self.executor.execute_tool_call(
            arguments=arguments,
            call_id=call_id,
        )

        await show_tool_result(
            self.presentation,
            PLAN_STEPS_TOOL,
            arguments,
            report,
            call_id=call_id,
        )
        return report

    async def execute_operation(
        self,
        invocation: ToolInvocation
    ) -> ToolOperationResult[ClientToolCallResult]:
        """把计划执行结果转换为统一客户端工具操作结果。"""
        report = await self.handle(invocation=invocation)
        result = ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=report.ok,
            text=report.text,
            cost_ms=report.cost_ms,
            call_id=invocation.call_id,
            fields=report.fields,
        )
        return ToolOperationResult(
            value=result,
            snapshot=ToolResultSnapshot(
                ok=result.ok,
                text=result.text,
                fields=result.fields,
            ),
            additional_context=report.additional_context,
        )


if __name__ == '__main__':
    pass
