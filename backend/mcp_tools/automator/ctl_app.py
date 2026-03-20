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
          - 向设备发送一次 VIEW deep link 启动请求。
          - 只负责执行跳转命令，不保证目标应用一定成功打开到预期页面。
          - 若系统没有可处理该 URL 的 handler，可能无效果或直接失败。
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
          - 启动指定应用。
          - 提供 activity 时按 package/activity 精确启动；不提供时启动系统解析到的默认入口。
          - 该工具只下发启动命令，不校验应用是否最终进入前台。
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
          - 强制停止指定包名对应的应用进程。
          - 适合在重启应用、清理运行态或回归前做状态归零。
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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "app"})
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
          - 安装本地 APK 到设备。
          - replace=True 使用替换安装；downgrade=True 允许降级；test=True 允许测试包。
          - 本地路径不存在、签名不兼容、权限不足或设备策略限制时会失败。
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
          - 卸载指定应用。
          - keep_data=True 时保留应用数据目录；False 时同时移除应用数据。
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
          - 清空指定应用的数据与缓存，但不卸载应用本体。
          - 适合登录态重置、首启场景回放或回归前清场。
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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "app"})
    @task_middleware("app_foreground")
    async def app_foreground(
        package: str,
        activity: typing.Optional[str] = None,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: app
        A: app_foreground
        P:
          package: str
          activity: str?
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 尝试把目标应用带到前台，并返回最终是否成功进入前台。
          - 内部流程是：先检查当前前台 -> 启动应用 -> 等待前台稳定命中。
          - 首次拉起失败时会执行一次 force-stop 后重试。
        """
        args = {
            "package"  : package,
            "activity" : activity
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.app_foreground(**a)

        return await broadcast(
            tool="app_foreground",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
