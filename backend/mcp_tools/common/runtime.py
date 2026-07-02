# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.common.schemas.schema_runtime import (
    DelayArg,
    LoopCountArg,
    LoopStepsArg,
    StopOnFailArg
)
from backend.utilities.runtime import (
    AppContext, Idle, loop_steps_output, sleep_output
)
from backend.utilities.tool_result import build_tool_result


def bind(mcp: FastMCP, idle: Idle, _: AppContext) -> None:

    @mcp.tool(
        description=(
            "按给定秒数执行一次固定等待。"
            " 该工具只负责时间延迟，不判断页面、任务或设备是否已经就绪。"
            " 需要等待具体状态时，应改用对应领域的显式检查或等待工具。"
        ),
        meta={"hidden": False, "domain": "common", "class": "runtime"}
    )
    @task_middleware("sleep")
    async def sleep(delay: DelayArg) -> CallToolResult:

        args = {
            "delay" : delay
        }

        job_id = await idle.job_begin("runtime.sleep", args=args)
        try:
            raw = await sleep_output(delay)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="sleep", args=args, raw=raw)

    @mcp.tool(
        description=(
            "声明一个可循环执行的步骤列表。"
            " 该工具只校验并返回标准化声明，不会真的执行 `steps`。"
            " `steps` 中每一项应包含 `tool` 和 `args`，且不允许嵌套 `loop_steps`。"
        ),
        meta={"hidden": False, "domain": "common", "class": "runtime"}
    )
    @task_middleware("loop_steps")
    async def loop_steps(
        loops: LoopCountArg,
        steps: LoopStepsArg,
        stop_on_fail: StopOnFailArg = True
    ) -> CallToolResult:

        args = {
            "loops"        : loops,
            "steps"        : steps,
            "stop_on_fail" : stop_on_fail
        }

        raw = loop_steps_output(
            loops=loops,
            steps=steps,
            stop_on_fail=stop_on_fail
        )

        return build_tool_result(tool="loop_steps", args=args, raw=raw)


if __name__ == '__main__':
    pass
