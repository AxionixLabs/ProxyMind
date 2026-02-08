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
        """
        D: common
        C: runtime
        A: sleep
        P:
          delay: float
        R: CTR
        N:
          - 固定时间等待，用于节奏控制/动画缓冲
          - 仅时间延迟 ≠ 页面就绪（需要时应配合 wait_* 断言）
        """

        args = {
            "delay" : delay
        }

        async def call(*_) -> None:
            job_id = await idle.job_begin(f"runtime.sleep", args=args)
            try:
                return await asyncio.sleep(delay)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="sleep",
            args=args,
            target_list=[None],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
