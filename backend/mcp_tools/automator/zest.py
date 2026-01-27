#  _____         _
# |__  /___  ___| |_
#   / // _ \/ __| __|
#  / /|  __/\__ \ |_
# /____\___||___/\__|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import asyncio
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("sleep")
    async def sleep(delay: float) -> CallToolResult:
        """Class: tool; Action: 固定等待; Args: delay(seconds float); Use: 稳定节奏/等待动画; Return: CallToolResult(text + structuredContent); Notes: 仅时间延迟≠页面就绪。"""

        async def call(*_) -> None:
            return await asyncio.sleep(delay)

        return await broadcast(
            tool="sleep", args={"delay": delay}, target_list=[None], call=call
        )

    @mcp.tool()
    @task_middleware("refresh")
    async def refresh(ttl_sec: float = 1.0) -> CallToolResult:
        """Class: tool; Action: 刷新设备列表(TTL缓存); Args: ttl_sec(float); Use: 执行前获取/更新可用设备; Return: {CallToolResult(text + structuredContent); Notes: ttl内复用缓存, 超时才重扫adb."""

        async def call(*_) -> dict:
            device_list = await manage.refresh(ttl_sec)
            return {
                "devices": len(device_list),
                "serials": [device.serial for device in device_list],
            }

        return await broadcast(
            tool="refresh", args={"ttl_sec": ttl_sec}, target_list=[None], call=call
        )


if __name__ == '__main__':
    pass
