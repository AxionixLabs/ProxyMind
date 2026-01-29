#  _____         _
# |__  /___  ___| |_
#   / // _ \/ __| __|
#  / /|  __/\__ \ |_
# /____\___||___/\__|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_hub.hub_monkey import Monkey
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import (
    broadcast, Idle
)


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:

    @mcp.tool()
    @task_middleware("sleep")
    async def sleep(delay: float) -> CallToolResult:
        """Class: tool; Action: 固定等待; Args: delay(float); Use: 稳定节奏/等待动画; Return: CallToolResult(text + structuredContent); Notes: 仅时间延迟≠页面就绪。"""

        async def call(*_) -> None:
            job_id = await idle.job_begin("tool.sleep", args={"delay": delay})
            try:
                return await asyncio.sleep(delay)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="sleep", args={"delay": delay}, target_list=[None], call=call
        )

    @mcp.tool()
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

    @mcp.tool()
    @task_middleware("monkey_injection")
    async def monkey_injection(
        package: str,
        seed: int = 42,
        throttle_ms: int = 150,
        touch: int = 65,
        motion: int = 20,
        nav: int = 10,
        events: int = 10000,
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


if __name__ == '__main__':
    pass
