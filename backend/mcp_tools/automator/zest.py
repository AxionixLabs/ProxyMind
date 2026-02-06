#  _____         _
# |__  /___  ___| |_
#   / // _ \/ __| __|
#  / /|  __/\__ \ |_
# /____\___||___/\__|
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
    @task_middleware("refresh")
    async def refresh(ttl_sec: float = 1.0) -> CallToolResult:
        """Class: tool; Action: 刷新设备列表(TTL缓存); Args: ttl_sec(float=1.0); Use: 执行前获取/更新可用设备; Return: {CallToolResult(text + structuredContent); Notes: ttl内复用缓存, 超时才重扫adb."""

        async def call(*_) -> dict:
            device_list = await manage.refresh(ttl_sec)
            return {
                "devices": len(device_list),
                "serials": [device.serial for device in device_list],
            }

        return await broadcast(
            tool="refresh", args={"ttl_sec": ttl_sec}, target_list=[None], call=call
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "tool"})
    @task_middleware("device_snapshot")
    async def device_snapshot() -> CallToolResult:
        """Class: tool; Action: 设备状态快照; Args: none; Use: 查看所有设备型号/状态/联网/屏幕/电量；Return: CallToolResult(text + structuredContent); Notes: 每台设备并发采集，失败设备返回异常结果。"""
        return await broadcast(
            tool="device_snapshot",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.device_snapshot()
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "tool"})
    @task_middleware("monkey_injection")
    async def monkey_injection(
        package: str,
        seed: int = 42,
        throttle_ms: int = 150,
        touch: int = 65,
        motion: int = 20,
        nav: int = 10,
        events: int = 10000
    ) -> CallToolResult:
        """Class: tool; Action: Monkey随机事件注入; Args: package(str), seed(int=42), throttle_ms(int=150), touch(int=65), motion(int=20), nav(int=10), events(int=10000); Use: 对指定包执行 adb shell monkey 并全量抓取 logcat，通过关键词命中方式收集 tail 证据; Return: CallToolResult(text + structuredContent); Notes: 内部会先 logcat -c，再启动 logcat 抓取（长连接），同时启动 monkey 注入，命中关键词才会打印并收集到 tail（降低噪音）。"""

        async def call(device: Device) -> dict[str, typing.Any]:
            monkey: Monkey = Monkey()
            job_id = await idle.job_begin(
                f"{monkey.agent_id}.monkey_injection",
                args={
                    "package"     : package,
                    "seed"        : seed,
                    "throttle_ms" : throttle_ms,
                    "touch"       : touch,
                    "motion"      : motion,
                    "nav"         : nav,
                    "events"      : events
                }
            )
            try:
                return await monkey.monkey_injection(
                    device, package, seed, throttle_ms, touch, motion, nav, events
                )
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="monkey_injection",
            args={
                "package"     : package,
                "seed"        : seed,
                "throttle_ms" : throttle_ms,
                "touch"       : touch,
                "motion"      : motion,
                "nav"         : nav,
                "events"      : events
            },
            target_list=manage.snapshot,
            call=call
        )

    @mcp.tool(meta={"hidden": True, "domain": "device", "class": "tool"})
    @task_middleware("click_matrix")
    async def click_matrix(matrix: dict[str, dict[str, typing.Any]]) -> CallToolResult:

        async def call(device: Device) -> dict[str, typing.Any]:
            cfg = (matrix or {}).get(device.serial)
            if not cfg:
                return {
                    "text" : "skipped(no args for serial)",
                    "data" : {"skipped": True}
                }
            return await device.click(cfg["by"], cfg["value"])

        return await broadcast(
            tool="click_matrix", args={"matrix": matrix}, target_list=manage.snapshot, call=call
        )


if __name__ == '__main__':
    pass
