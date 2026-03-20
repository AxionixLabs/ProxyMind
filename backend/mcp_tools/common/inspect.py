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
          - 查询当前运行时的空闲与任务状态快照。
          - 返回的是运行时观测信息，不会触发任何新任务。
          - 适合在批跑、回填或收束前判断当前是否还有挂起任务。
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
          - 声明一次自由规则判断请求。
          - 该工具只透传 `message` 和 `context`，不直接完成模型调用或规则求值。
          - 真正的调用、增强和结果解析由上层执行链接管。
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
