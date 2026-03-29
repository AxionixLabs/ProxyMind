# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from pydantic import Field
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_tools.shared import MatrixArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


ScreenshotLocalArg = typing.Annotated[
    typing.Optional[str],
    Field(description="截图保存目录或基准路径；为空时由底层工具按默认规则命名。"),
]
PackageKeywordArg = typing.Annotated[
    typing.Optional[str],
    Field(description="包名过滤关键字；为空时按 `scope` 返回完整候选列表。"),
]
PackageScopeArg = typing.Annotated[
    typing.Literal["user", "system", "all"],
    Field(description="包名查询范围，可选用户应用、系统应用或全部应用。"),
]


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool(
        description=(
            "采集设备当前状态快照，包括基础属性、联网状态、屏幕状态和电量等信息。"
            " 该工具只做观测，不会修改设备状态。"
            " 多设备场景下逐台并发采集，单台失败不会阻断其他设备。"
        ),
        meta={"hidden": False, "domain": "device", "class": "info"}
    )
    @task_middleware("device_snapshot")
    async def device_snapshot(
        matrix: MatrixArg = None
    ) -> CallToolResult:
        async def call(device: Device, *_) -> typing.Any:
            return await device.device_snapshot()

        return await broadcast(
            tool="device_snapshot",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "截取设备当前屏幕并返回本地附件路径。"
            " 提供 `local` 时会作为保存目录或基准路径使用，多设备执行时会按 serial 区分文件名。"
            " 设备不可用、截图失败或目标路径不可写时调用会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "info"}
    )
    @task_middleware("screenshot")
    async def screenshot(
        local: ScreenshotLocalArg = None,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "local" : local
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.screenshot(**a)

        return await broadcast(
            tool="screenshot",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "列出设备上已安装的包名。"
            " `keyword` 为空时按 `scope` 返回整类包，非空时做大小写不敏感的包含过滤。"
            " `scope` 只影响候选范围，不会检查应用当前运行状态。"
        ),
        meta={"hidden": False, "domain": "device", "class": "info"}
    )
    @task_middleware("grep_packages")
    async def grep_packages(
        keyword: PackageKeywordArg = None,
        scope: PackageScopeArg = "user",
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "keyword" : keyword,
            "scope"   : scope
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.grep_packages(**a)

        return await broadcast(
            tool="grep_packages",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
