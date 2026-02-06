#  ___                           _
# |_ _|_ __  ___ _ __   ___  ___| |_
#  | || '_ \/ __| '_ \ / _ \/ __| __|
#  | || | | \__ \ |_) |  __/ (__| |_
# |___|_| |_|___/ .__/ \___|\___|\__|
#               |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "inspect"})
    @task_middleware("query_idle")
    async def query_idle() -> CallToolResult:
        """Class: inspect; Action: 查询服务运行状态/后台任务数量/任务概览; Args: none; Use: 获取当前运行中任务、后台队列/并发占用、空闲程度与可接新任务能力; Return: CallToolResult(text + structuredContent); Notes: 服务状态快照。"""

        async def call(*_) -> dict:
            return await idle.snapshot()

        return await broadcast(
            tool="query_idle", args={}, target_list=[idle], call=call
        )


if __name__ == '__main__':
    pass
