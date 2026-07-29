# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.mcp.contracts import McpSessionLike
from mind_app.runtime.execution import (
    ToolInvocation,
    TurnContext
)
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from mind_app.output import (
    OutputControlPort,
    OutputStatusPort
)
from mind_app.presentation.contracts import PresentationSink
from mind_app.presentation.plan_views import build_plan_steps_start_view
from .display import show_tool_result
from .plan_steps import (
    PlanExecutionReport,
    StepPlanExecutor
)


class PlanToolCallRunner:
    """编排 plan_steps 的展示和执行。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        output_control: OutputControlPort,
        status_control: OutputStatusPort,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        report: typing.Any,
        turn_context: TurnContext,
        tool_call_coordinator: ToolCallCoordinator
    ) -> None:
        self.output_control = output_control
        self.status_control = status_control
        self.presentation   = presentation

        self.executor = StepPlanExecutor(
            session=session,
            tools=tools,
            report=report,
            turn_context=turn_context,
            tool_call_coordinator=tool_call_coordinator,
        )

    async def handle(
        self,
        *,
        invocation: ToolInvocation
    ) -> "PlanExecutionReport":
        """处理一次完整的 plan_steps 工具调用。"""
        arguments = dict(invocation.arguments)
        call_id   = invocation.call_id

        self.output_control.record_tool_arguments(
            PLAN_STEPS_TOOL,
            arguments,
            call_id=call_id,
        )

        await self.presentation.emit(
            build_plan_steps_start_view(arguments)
        )

        await self.status_control.begin_tool_status()

        try:
            report = await self.executor.execute_tool_call(
                arguments=arguments,
                call_id=call_id,
            )
        finally:
            await self.status_control.end_status(immediate=True)

        await show_tool_result(
            self.presentation,
            PLAN_STEPS_TOOL,
            arguments,
            report,
            call_id=call_id,
        )
        return report


if __name__ == '__main__':
    pass
