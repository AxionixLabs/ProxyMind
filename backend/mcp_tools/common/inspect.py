# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.tool_result import build_tool_result


def bind(mcp: FastMCP, idle: Idle, _: AppContext) -> None:

    @mcp.tool(
        description=(
            "查询当前运行时的空闲与任务状态快照。"
            " 该工具只返回观测信息，不会触发任何新任务。"
            " 适合在批跑、回填或收束前判断当前是否还有挂起任务。"
        ),
        meta={"hidden": False, "domain": "common", "class": "inspect"}
    )
    @task_middleware("query_idle")
    async def query_idle() -> CallToolResult:
        snapshot = await idle.snapshot()
        raw = {
            "ok"          : True,
            "text"        : "idle snapshot",
            "attachments" : [],
            "data"        : snapshot,
            "logs"        : []
        }
        return build_tool_result(tool="query_idle", args={}, raw=raw)


if __name__ == '__main__':
    pass
