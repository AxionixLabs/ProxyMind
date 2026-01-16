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
    async def app_start(package: str, activity: typing.Optional[str] = None) -> typing.Any:
        """Class: app; Action: 启动应用(am/monkey); Args: package(str), activity(str|None); Use: 打开/启动某应用，可指定 Activity 精确启动; Return: list[device_result]; Notes: activity 为空则走 monkey 启动主入口，非空则 am start -n package/activity."""
        device_list = await manage.refresh()

        logger.info(f"App start {package}")
        return await asyncio.gather(
            *(device.app_start(package, activity) for device in device_list), return_exceptions=True
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

    @mcp.tool()
    @task_middleware("app_install")
    async def app_install(apk: str, replace: bool = True, downgrade: bool = False, test: bool = False) -> typing.Any:
        """Class: app; Action: 安装APK; Args: apk(str), replace(bool), downgrade(bool), test(bool); Use: 安装/部署某应用；Return: list[device_result]; Notes: replace=-r, downgrade=-d, test=-t."""
        device_list = await manage.refresh()

        logger.info(
            f"App install apk={apk} replace={replace} downgrade={downgrade} test={test}"
        )

        return await asyncio.gather(
            *(device.app_install(apk, replace, downgrade, test)
              for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("app_uninstall")
    async def app_uninstall(package: str, keep_data: bool = False) -> typing.Any:
        """Class: app; Action: 卸载应用; Args: package(str), keep_data(bool); Use: 卸载/移除某应用；Return: list[device_result]; Notes: keep_data=True 时使用 pm uninstall -k 保留数据目录。"""
        device_list = await manage.refresh()

        logger.info(f"App uninstall package={package} keep_data={keep_data}")
        return await asyncio.gather(
            *(device.app_uninstall(package, keep_data=keep_data)
              for device in device_list), return_exceptions=True
        )


if __name__ == '__main__':
    pass
