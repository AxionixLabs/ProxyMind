#     _                   ____            _             _
#    / \   _ __  _ __    / ___|___  _ __ | |_ _ __ ___ | |
#   / _ \ | '_ \| '_ \  | |   / _ \| '_ \| __| '__/ _ \| |
#  / ___ \| |_) | |_) | | |__| (_) | | | | |_| | | (_) | |
# /_/   \_\ .__/| .__/   \____\___/|_| |_|\__|_|  \___/|_|
#         |_|   |_|
#

import typing
import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("deep_link")
    async def deep_link(url: str) -> typing.Any:
        """Class: app; Action: 深链跳转(am start VIEW); Args: url(str); Use: 直达应用内部页面/服务; Return: list[device_result]; Notes: 需系统存在 handler."""
        device_list = await manage.refresh()

        logger.info(f"Deep link {url}")
        return await asyncio.gather(
            *(device.deep_link(url) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("app_start")
    async def app_start(package: str) -> typing.Any:
        """Class: app; Action: 启动应用(monkey); Args: package(str); Use: 用户语义“打开/启动某应用”优先; Return: list[device_result]; Notes: 启动主入口, 非指定 Activity."""
        device_list = await manage.refresh()

        logger.info(f"App start {package}")
        return await asyncio.gather(
            *(device.app_start(package) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("app_stop")
    async def app_stop(package: str) -> typing.Any:
        """Class: app; Action: 强制停止应用(force-stop); Args: package(str); Use: 重启应用/清理状态; Return: list[device_result]; Notes: 终止进程与后台任务."""
        device_list = await manage.refresh()

        logger.info(f"App stop {package}")
        return await asyncio.gather(
            *(device.app_stop(package) for device in device_list), return_exceptions=True
        )


if __name__ == '__main__':
    pass
