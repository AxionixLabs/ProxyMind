# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.mcp import McpSessionLike
from mind_app.stream_ui import StreamUI
from mind_nova import request
from .display import (
    show_tool_result,
    show_tool_start
)
from .plan_steps import StepPlanExecutor


class PlanToolCallRunner:
    """编排 plan_steps 的展示、执行和结果回传。"""

    def __init__(
        self,
        *,
        session: McpSessionLike,
        stream_ui: StreamUI,
        tools: list[dict[str, typing.Any]],
        report: typing.Any
    ) -> None:
        self.stream_ui = stream_ui

        self.executor = StepPlanExecutor(
            session=session,
            stream_ui=stream_ui,
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

        await show_tool_start(
            self.stream_ui,
            PLAN_STEPS_TOOL,
            arguments,
            call_id=str(event.get("call_id") or "")
        )

        report = await self.executor.execute_tool_call(arguments=arguments)

        await show_tool_result(
            self.stream_ui,
            PLAN_STEPS_TOOL,
            arguments,
            report
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
