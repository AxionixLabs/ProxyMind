#  ____                  _       _   _
# | __ )  ___ _ __   ___| |__   | \ | | _____  ___   _ ___
# |  _ \ / _ \ '_ \ / __| '_ \  |  \| |/ _ \ \/ / | | / __|
# | |_) |  __/ | | | (__| | | | | |\  |  __/>  <| |_| \__ \
# |____/ \___|_| |_|\___|_| |_| |_| \_|\___/_/\_\\__,_|___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.middlewares.mid_task import task_middleware
from backend.utilities.instance import Ins
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "bench", "class": "nexus"})
    @task_middleware("nexus_flow")
    async def nexus_flow(payload: dict[str, typing.Any],concurrency: int = 1) -> CallToolResult:

        args = {
            "payload"     : payload,
            "concurrency" : concurrency
        }

        async def call(*_) -> typing.Any:
            job_id = await idle.job_begin(f"{Ins.nexus.agent_id}.nexus_flow", args=args)
            try:
                return await Ins.nexus.flow(**args)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="nexus_flow",
            args=args,
            target_list=[Ins.nexus],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
