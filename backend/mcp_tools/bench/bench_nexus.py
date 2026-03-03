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
        """
        D: bench
        C: nexus
        A: nexus_flow
        P:
          payload: dict  # 统一入口参数
            - mode: oneof(http|sse|ws|flow)="flow"
            - env?: {base_url?: str, headers?: dict[str,str], timeout_s?: float}
            - http:  method/url/headers/params/json/json_body/body/body_text/timeout_s/retries/follow_redirects
            - sse:   url/base_url/headers/params/timeout_s/max_events
            - ws:    url/headers/sends/timeout_s/max_messages
            - flow:  vars/options/steps + 任意自定义字段
              - options?: {fail_fast?: bool}
          concurrency: int=1  # flow 模式下 steps 并发度（Semaphore）
        R: CTR
        N:
          - 统一入口：按 payload.mode 分流执行（http/sse/ws/flow）
          - flow 模式：steps 支持并发；失败策略由 options.fail_fast 控制
        """
        
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
