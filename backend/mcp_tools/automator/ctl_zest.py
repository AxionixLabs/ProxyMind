#   ____ _____ _       _____         _
#  / ___|_   _| |     |__  /___  ___| |_
# | |     | | | |       / // _ \/ __| __|
# | |___  | | | |___   / /|  __/\__ \ |_
#  \____| |_| |_____| /____\___||___/\__|
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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "tool"})
    @task_middleware("device_snapshot")
    async def device_snapshot(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: tool
        A: device_snapshot
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 采集所有在线设备的状态快照（型号/联网/屏幕/电量等）
          - 并发采集：单设备失败不影响其他设备（失败以 per-device 结果体现）
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.device_snapshot()

        return await broadcast(
            tool="device_snapshot",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "tool"})
    @task_middleware("screenshot")
    async def screenshot(
        local: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: tool
        A: screenshot
        P:
          local: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 多设备同写一个 local 不会覆盖/冲突（按 serial 分文件名）
        """

        args = {
            "local": local
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.screenshot(**a)

        return await broadcast(
            tool="screenshot",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "tool"})
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
        C: tool
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
