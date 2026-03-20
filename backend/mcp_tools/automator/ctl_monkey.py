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
          - 对指定应用执行一次 `adb shell monkey` 事件注入。
          - 只注入 touch、motion、nav 三类事件，并固定关闭 appswitch 与 syskeys 百分比。
          - 执行前会清空 logcat，执行期间持续抓取命中的异常证据 tail，用于回传 crash/anr 等线索。
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
            job_id = await idle.job_begin("monkey.injection", args=a)
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
