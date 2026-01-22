#  __  __          _ _          ____            _             _
# |  \/  | ___  __| (_) __ _   / ___|___  _ __ | |_ _ __ ___ | |
# | |\/| |/ _ \/ _` | |/ _` | | |   / _ \| '_ \| __| '__/ _ \| |
# | |  | |  __/ (_| | | (_| | | |__| (_) | | | | |_| | | (_) | |
# |_|  |_|\___|\__,_|_|\__,_|  \____\___/|_| |_|\__|_|  \___/|_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from backend.mcp_hub.hub_manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("screenshot")
    async def screenshot() -> typing.Any:
        """Class: media; Action: 截取当前屏幕截图; Args: none; Use: 取证/调试/执行后验证; Return: list[device_result]; Notes: 截图结果（可能包含路径/bytes/metadata，依 device 实现而定）"""
        device_list = manage.snapshot

        logger.info("Screenshot")
        return await asyncio.gather(
            *(device.screenshot() for device in device_list), return_exceptions=True
        )


if __name__ == '__main__':
    pass
