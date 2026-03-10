#  ___                           _
# |_ _|_ __  ___ _ __   ___  ___| |_
#  | || '_ \/ __| '_ \ / _ \/ __| __|
#  | || | | \__ \ |_) |  __/ (__| |_
# |___|_| |_|___/ .__/ \___|\___|\__|
#               |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "inspect"})
    @task_middleware("query_idle")
    async def query_idle() -> CallToolResult:
        """
        D: common
        C: inspect
        A: query_idle
        P:
          none
        R: CTR
        N:
          - 查询服务状态快照：运行状态/后台任务数量/任务概览
          - 用于判断并发占用、空闲程度与是否可接新任务
        """

        async def call(*_) -> dict:
            return await idle.snapshot()

        return await broadcast(
            tool="query_idle",
            args={},
            target_list=[idle],
            call=call,
            overrides=None
        )

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "inspect"})
    @task_middleware("free_rule")
    async def free_rule(
        message: str,
        context: typing.Optional[dict[str, typing.Any]] = None
    ) -> CallToolResult:
        """
        D: common
        C: inspect
        A: free_rule
        P:
          message: str
          context: dict?=None  # 执行期聚合上下文（如 plan/step/tool 结果）
        R: CTR
        N:
          - 用途：调用远程大模型执行通用能力（断言/闲聊/评价/打分/规则判断等）
          - 本工具仅透传 message/context（原样回传）；实际调用与结果解析由增强层接管
        """

        args = {
            "message" : message,
            "context" : context
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin("free_rule", args=args)
            try:
                return args
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="free_rule",
            args=args,
            target_list=[None],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
