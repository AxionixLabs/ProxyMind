#  ____              _   _
# |  _ \ _   _ _ __ | |_(_)_ __ ___   ___
# | |_) | | | | '_ \| __| | '_ ` _ \ / _ \
# |  _ <| |_| | | | | |_| | | | | | |  __/
# |_| \_\\__,_|_| |_|\__|_|_| |_| |_|\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import asyncio
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "common", "class": "runtime"})
    @task_middleware("sleep")
    async def sleep(delay: float) -> CallToolResult:
        """Class: runtime; Action: 固定等待; Args: delay(float); Use: 稳定节奏/等待动画; Return: CallToolResult(text + structuredContent); Notes: 仅时间延迟≠页面就绪。"""

        async def call(*_) -> None:
            job_id = await idle.job_begin("runtime.sleep", args={"delay": delay})
            try:
                return await asyncio.sleep(delay)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="sleep", args={"delay": delay}, target_list=[None], call=call
        )


if __name__ == '__main__':
    pass
