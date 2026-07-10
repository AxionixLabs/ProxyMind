# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.common.schemas.schema_runtime import (
    DelayArg
)
from backend.utilities.runtime import (
    AppContext, Idle, sleep_output
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


if __name__ == '__main__':
    pass
