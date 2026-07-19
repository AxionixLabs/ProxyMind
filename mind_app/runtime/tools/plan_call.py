# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.mcp import McpSessionLike
from mind_app.output import OutputControlPort
from mind_app.presentation.contracts import PresentationSink
from mind_app.presentation.plan_views import build_plan_steps_start_view
from mind_nova import request
from .display import show_tool_result
from .plan_steps import StepPlanExecutor


class PlanToolCallRunner:
    """编排 plan_steps 的展示、执行和结果回传。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        stream_ui: OutputControlPort,
        presentation: PresentationSink,
        tools: list[dict[str, typing.Any]],
        report: typing.Any
    ) -> None:
        self.stream_ui    = stream_ui
        self.presentation = presentation

        self.executor = StepPlanExecutor(
            session=session,
            tools=tools,
            report=report
        )

    async def handle(
        self,
        *,
        event: dict[str, typing.Any],
        arguments: dict[str, typing.Any]
    ) -> None:
        """处理一次完整的 plan_steps 工具调用。"""
        execution = event.get("execution")

        self.stream_ui.record_tool_arguments(
            PLAN_STEPS_TOOL,
            arguments,
            call_id=str(event.get("call_id") or "")
        )

        await self.presentation.emit(
            build_plan_steps_start_view(arguments)
        )

        await self.stream_ui.begin_tool_status()

        try:
            report = await self.executor.execute_tool_call(
                arguments=arguments,
                cid=str(event.get("cid") or ""),
                sid=str(event.get("sid") or ""),
                call_id=str(event.get("call_id") or ""),
            )
        finally:
            await self.stream_ui.end_status(immediate=True)

        await show_tool_result(
            self.presentation,
            PLAN_STEPS_TOOL,
            arguments,
            report,
            call_id=str(event.get("call_id") or ""),
        )

        await request.post_tool_result(
            event["cid"],
            event["sid"],
            event["call_id"],
            PLAN_STEPS_TOOL,
            report.ok,
            report.fields,
            execution=execution if isinstance(execution, dict) else None
        )


if __name__ == '__main__':
    pass
