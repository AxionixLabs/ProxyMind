# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.mcp.contracts import McpSessionLike
from mind_app.runtime.execution import TurnContext
from mind_app.runtime.hooks.tool import ToolCallCoordinator
from mind_app.output import (
    OutputControlPort,
    OutputStatusPort
)
from mind_app.presentation.contracts import PresentationSink
from mind_app.presentation.plan_views import build_plan_steps_start_view
from mind_nova.requests.tools import post_tool_result
from .display import show_tool_result
from .plan_steps import (
    PlanExecutionReport,
    StepPlanExecutor
)


class PlanToolCallRunner:
    """编排 plan_steps 的展示、执行和结果回传。"""

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
        event: dict[str, typing.Any],
        arguments: dict[str, typing.Any]
    ) -> "PlanExecutionReport":
        """处理一次完整的 plan_steps 工具调用。"""
        execution = event.get("execution")

        self.output_control.record_tool_arguments(
            PLAN_STEPS_TOOL,
            arguments,
            call_id=str(event.get("call_id") or "")
        )

        await self.presentation.emit(
            build_plan_steps_start_view(arguments)
        )

        await self.status_control.begin_tool_status()

        try:
            report = await self.executor.execute_tool_call(
                arguments=arguments,
                call_id=str(event.get("call_id") or ""),
            )
        finally:
            await self.status_control.end_status(immediate=True)

        await show_tool_result(
            self.presentation,
            PLAN_STEPS_TOOL,
            arguments,
            report,
            call_id=str(event.get("call_id") or ""),
        )

        await post_tool_result(
            event["cid"],
            event["sid"],
            event["call_id"],
            PLAN_STEPS_TOOL,
            report.ok,
            report.fields,
            execution=execution if isinstance(execution, dict) else None
        )
        return report


if __name__ == '__main__':
    pass
