#     _                   ____            _             _
#    / \   _ __  _ __    / ___|___  _ __ | |_ _ __ ___ | |
#   / _ \ | '_ \| '_ \  | |   / _ \| '_ \| __| '__/ _ \| |
#  / ___ \| |_) | |_) | | |__| (_) | | | | |_| | | (_) | |
# /_/   \_\ .__/| .__/   \____\___/|_| |_|\__|_|  \___/|_|
#         |_|   |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("deep_link")
    async def deep_link(url: str) -> CallToolResult:
        """Class: app; Action: 深度链接跳转(am start VIEW); Args: url(str); Use: 直达应用内部页面/服务; Return: CallToolResult(text + structuredContent); Notes: 需系统存在 handler."""
        return await broadcast(
            tool="deep_link",
            args={"url": url},
            target_list=manage.snapshot,
            call=lambda agent: agent.deep_link(url)
        )

    @mcp.tool()
    @task_middleware("app_start")
    async def app_start(package: str, activity: typing.Optional[str] = None) -> CallToolResult:
        """Class: app; Action: 启动应用(am/monkey); Args: package(str), activity(str|None); Use: 打开/启动某应用，可指定 Activity 精确启动; Return: CallToolResult(text + structuredContent); Notes: activity 为空则走 monkey 启动主入口，非空则 am start -n package/activity."""
        return await broadcast(
            tool="app_start",
            args={"package": package, "activity": activity},
            target_list=manage.snapshot,
            call=lambda agent: agent.app_start(package, activity)
        )

    @mcp.tool()
    @task_middleware("app_stop")
    async def app_stop(package: str) -> typing.Any:
        """Class: app; Action: 强制停止应用(force-stop); Args: package(str); Use: 重启应用/清理状态; Return: CallToolResult(text + structuredContent); Notes: 终止进程与后台任务."""
        return await broadcast(
            tool="app_stop",
            args={"package": package},
            target_list=manage.snapshot,
            call=lambda agent: agent.app_stop(package)
        )

    @mcp.tool()
    @task_middleware("app_install")
    async def app_install(apk: str, replace: bool = True, downgrade: bool = False, test: bool = False) -> CallToolResult:
        """Class: app; Action: 安装APK; Args: apk(str), replace(bool), downgrade(bool), test(bool); Use: 安装/部署某应用；Return: CallToolResult(text + structuredContent); Notes: replace=-r, downgrade=-d, test=-t."""
        return await broadcast(
            tool="app_install",
            args={"apk": apk, "replace": replace, "downgrade": downgrade, "test": test},
            target_list=manage.snapshot,
            call=lambda agent: agent.app_install(apk, replace, downgrade, test)
        )

    @mcp.tool()
    @task_middleware("app_uninstall")
    async def app_uninstall(package: str, keep_data: bool = False) -> CallToolResult:
        """Class: app; Action: 卸载应用; Args: package(str), keep_data(bool); Use: 卸载/移除某应用；Return: CallToolResult(text + structuredContent); Notes: keep_data=True 时使用 pm uninstall -k 保留数据目录。"""
        return await broadcast(
            tool="app_uninstall",
            args={"package": package, "keep_data": keep_data},
            target_list=manage.snapshot,
            call=lambda agent: agent.app_uninstall(package, keep_data=keep_data)
        )

    @mcp.tool()
    @task_middleware("app_clear")
    async def app_clear(package: str) -> CallToolResult:
        """Class: app; Action: 清除应用数据; Args: package(str); Use: 清除应用数据/重置应用；Return: CallToolResult(text + structuredContent); Notes: 等价于 pm clear， 不卸载应用。"""
        return await broadcast(
            tool="app_clear",
            args={"package": package},
            target_list=manage.snapshot,
            call=lambda agent: agent.app_clear(package)
        )


if __name__ == '__main__':
    pass
