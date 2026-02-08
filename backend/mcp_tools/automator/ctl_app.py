#   ____ _____ _          _
#  / ___|_   _| |        / \   _ __  _ __
# | |     | | | |       / _ \ | '_ \| '_ \
# | |___  | | | |___   / ___ \| |_) | |_) |
#  \____| |_| |_____| /_/   \_\ .__/| .__/
#                             |_|   |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "app"})
    @task_middleware("app_deep_link")
    async def app_deep_link(
        url: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: app
        A: app_deep_link
        P:
          url: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 深度链接跳转（am start VIEW）
          - 需系统存在对应 handler，否则可能失败/无效果
        """

        args = {
            "url" : url
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.app_deep_link(**a)

        return await broadcast(
            tool="app_deep_link",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "app"})
    @task_middleware("app_start")
    async def app_start(
        package: str,
        activity: typing.Optional[str] = None,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: app
        A: app_start
        P:
          package: str
          activity: str?=None
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 启动应用：activity 指定则精确启动；不指定则启动主入口
        """

        args = {
            "package"  : package,
            "activity" : activity
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.app_start(**a)

        return await broadcast(
            tool="app_start",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "app"})
    @task_middleware("app_stop")
    async def app_stop(
        package: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: app
        A: app_stop
        P:
          package: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 强制停止应用（终止进程与后台任务），用于重启/清理状态
        """

        args = {
            "package" : package
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.app_stop(**a)

        return await broadcast(
            tool="app_stop",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description="安装 APK：支持替换安装(-r)/降级安装(-d)/测试包(-t) 等选项。",
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_install")
    async def app_install(
        apk: str,
        replace: bool = True,
        downgrade: bool = False,
        test: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: app
        A: app_install
        P:
          apk: str
          replace: bool=True
          downgrade: bool=False
          test: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 安装 APK：replace=-r（替换安装），downgrade=-d（降级安装），test=-t（测试包）
          - 输入路径不可用/签名不匹配/权限受限会失败
        """

        args = {
            "apk"       : apk,
            "replace"   : replace,
            "downgrade" : downgrade,
            "test"      : test
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.app_install(**a)

        return await broadcast(
            tool="app_install",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "app"})
    @task_middleware("app_uninstall")
    async def app_uninstall(
        package: str,
        keep_data: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: app
        A: app_uninstall
        P:
          package: str
          keep_data: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 卸载应用；keep_data=True 时保留数据目录（pm uninstall -k）
        """

        args = {
            "package"   : package,
            "keep_data" : keep_data
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.app_uninstall(**a)

        return await broadcast(
            tool="app_uninstall",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "app"})
    @task_middleware("app_clear")
    async def app_clear(
        package: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: app
        A: app_clear
        P:
          package: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 清除应用数据（pm clear），不卸载应用，用于重置状态
        """

        args = {
            "package" : package
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.app_clear(**a)

        return await broadcast(
            tool="app_clear",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
