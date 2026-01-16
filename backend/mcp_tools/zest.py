#  _____         _
# |__  /___  ___| |_
#   / // _ \/ __| __|
#  / /|  __/\__ \ |_
# /____\___||___/\__|
#

import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("sleep")
    async def sleep(delay: float) -> None:
        """Class: tool; Action: 固定等待; Args: delay(seconds float); Use: 稳定节奏/等待动画; Return: None; Notes: 仅时间延迟≠页面就绪."""
        logger.info(f"Wait {delay}")
        return await asyncio.sleep(delay)

    @mcp.tool()
    @task_middleware("refresh_with_ttl")
    async def refresh_with_ttl(ttl_sec: float = 1.0) -> dict:
        """Class: tool; Action: 刷新设备列表(TTL缓存); Args: ttl_sec(float); Use: 执行前获取/更新可用设备; Return: {devices:int,serials:list[str]}; Notes: ttl内复用缓存, 超时才重扫adb."""
        device_list = await manage.refresh_with_ttl(ttl_sec)

        return {
            "devices": len(device_list),
            "serials": [device.serial for device in device_list],
        }


if __name__ == '__main__':
    pass
