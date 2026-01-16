#  ____             _            ___        __
# |  _ \  _____   _(_) ___ ___  |_ _|_ __  / _| ___
# | | | |/ _ \ \ / / |/ __/ _ \  | || '_ \| |_ / _ \
# | |_| |  __/\ V /| | (_|  __/  | || | | |  _| (_) |
# |____/ \___| \_/ |_|\___\___| |___|_| |_|_|  \___/
#

import typing
import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("snapshot")
    async def snapshot() -> typing.Any:
        """Class: device; Action: 采集设备状态快照; Args: none; Use: 查看所有设备型号/状态/联网/屏幕/电量；Return: list[device_result]; Notes: 每台设备并发采集，失败设备返回异常结果。"""
        device_list = manage.snapshot

        logger.info("Get device snapshot")
        return await asyncio.gather(
            *(device.snapshot() for device in device_list), return_exceptions=True
        )


if __name__ == '__main__':
    pass
