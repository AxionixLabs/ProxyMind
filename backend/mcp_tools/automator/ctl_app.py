# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from pydantic import Field
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_tools.shared import MatrixArg, PackageArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


UrlArg = typing.Annotated[
    str,
    Field(description="要发送给系统处理的 deep link URL。"),
]
ActivityArg = typing.Annotated[
    typing.Optional[str],
    Field(description="目标 Activity；为空时使用应用默认入口。"),
]
ApkPathArg = typing.Annotated[
    str,
    Field(description="本地 APK 文件路径。"),
]
ReplaceArg = typing.Annotated[
    bool,
    Field(description="安装时若应用已存在，是否允许覆盖安装。"),
]
DowngradeArg = typing.Annotated[
    bool,
    Field(description="安装时是否允许版本降级。"),
]
TestOnlyArg = typing.Annotated[
    bool,
    Field(description="是否按 test APK 方式安装。"),
]
KeepDataArg = typing.Annotated[
    bool,
    Field(description="卸载应用时是否保留应用数据目录。"),
]


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool(
        description=(
            "向目标设备发送一次 deep link 启动请求。"
            " 该工具只负责下发跳转命令，不保证目标应用一定打开到预期页面。"
            " 如果系统没有可处理该 URL 的 handler，调用可能无效果或直接失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_deep_link")
    async def app_deep_link(
        url: UrlArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "启动指定应用。"
            " 该工具只下发启动命令，不校验应用是否最终进入前台。"
            " 提供 `activity` 时会按 package/activity 精确启动；不提供时使用系统解析到的默认入口。"
        ),
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_start")
    async def app_start(
        package: PackageArg,
        activity: ActivityArg = None,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "强制停止指定包名对应的应用进程。"
            " 该工具用于运行态归零，不会自动重启应用或校验最终前台状态。"
            " 适合在重启应用、清理残留状态或回归前清场时使用。"
        ),
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_stop")
    async def app_stop(
        package: PackageArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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
        description=(
            "安装本地 APK 到设备。"
            " 该工具只负责安装，不会自动启动应用或处理签名兼容问题。"
            " 本地文件不存在、设备策略限制、签名不兼容或降级未开启时会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_install")
    async def app_install(
        apk: ApkPathArg,
        replace: ReplaceArg = True,
        downgrade: DowngradeArg = False,
        test: TestOnlyArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "卸载指定应用。"
            " `keep_data` 为 true 时保留应用数据目录，否则同时移除应用数据。"
            " 系统应用、权限受限设备或被策略保护的包可能无法卸载。"
        ),
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_uninstall")
    async def app_uninstall(
        package: PackageArg,
        keep_data: KeepDataArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "清空指定应用的数据与缓存，但不卸载应用本体。"
            " 该工具适合重置登录态、首启状态或本地缓存，不会自动重启应用。"
            " 包名不存在或设备权限不足时会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_clear")
    async def app_clear(
        package: PackageArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "尝试把目标应用带到前台，并返回是否成功进入前台。"
            " 该工具会先检查当前前台，再执行启动并等待前台稳定命中。"
            " 首次拉起失败时会执行一次 force-stop 后重试，因此它比 `app_start` 更适合前台验收场景。"
        ),
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_foreground")
    async def app_foreground(
        package: PackageArg,
        activity: ActivityArg = None,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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
