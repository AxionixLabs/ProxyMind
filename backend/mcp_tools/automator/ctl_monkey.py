#   ____ _____ _       __  __             _
#  / ___|_   _| |     |  \/  | ___  _ __ | | _____ _   _
# | |     | | | |     | |\/| |/ _ \| '_ \| |/ / _ \ | | |
# | |___  | | | |___  | |  | | (_) | | | |   <  __/ |_| |
#  \____| |_| |_____| |_|  |_|\___/|_| |_|_|\_\___|\__, |
#                                                  |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_hub.hub_monkey import Monkey
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "monkey"})
    @task_middleware("injection")
    async def injection(
        package: str,
        seed: int = 42,
        throttle_ms: int = 150,
        touch: int = 65,
        motion: int = 20,
        nav: int = 10,
        events: int = 10000,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: monkey
        A: monkey_injection
        P:
          package: str
          seed: int=42
          throttle_ms: int=150
          touch: int=65
          motion: int=20
          nav: int=10
          events: int=10000
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 对指定包执行 `adb shell monkey` 并抓取 logcat 证据
          - 内部流程：logcat -c -> 启动 logcat 长连接 -> monkey 注入；按关键词命中收集 tail（降低噪音）
        """

        args = {
            "package"     : package,
            "seed"        : seed,
            "throttle_ms" : throttle_ms,
            "touch"       : touch,
            "motion"      : motion,
            "nav"         : nav,
            "events"      : events
        }

        async def call(device: Device, a: dict) -> typing.Any:
            job_id = await idle.job_begin(f"tool.injection", args=a)
            monkey: Monkey = Monkey()
            try:
                return await monkey.injection(device, **a)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="injection",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
