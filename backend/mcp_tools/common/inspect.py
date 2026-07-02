# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.mcp_tools.common.schemas.schema_inspect import (
    FreeRuleMessageArg,
    FreeRuleContextArg
)
from backend.utilities.runtime import (
    AppContext, Idle, free_rule_output
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

    @mcp.tool(
        description=(
            "声明一次自由规则判断请求。"
            " 该工具只透传 `message` 和 `context`，不直接完成模型调用或规则求值。"
            " 真正的调用、增强和结果解析由上层执行链处理。"
        ),
        meta={"hidden": False, "domain": "common", "class": "inspect"}
    )
    @task_middleware("free_rule")
    async def free_rule(
        message: FreeRuleMessageArg,
        context: FreeRuleContextArg = None
    ) -> CallToolResult:

        args = {
            "message" : message,
            "context" : context
        }

        job_id = await idle.job_begin("free_rule", args=args)
        try:
            raw = free_rule_output(args)
        finally:
            await idle.job_final(job_id)

        return build_tool_result(tool="free_rule", args=args, raw=raw)


if __name__ == '__main__':
    pass
