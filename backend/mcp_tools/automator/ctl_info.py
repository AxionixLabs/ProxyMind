# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.utilities.tool_result import build_tool_result
from backend.mcp_tools.automator.schemas.schema_info import (
    PackageKeywordArg,
    PackageScopeArg,
    ScreenshotLocalArg
)
from backend.mcp_tools.shared import SerialArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext


def bind(mcp: FastMCP, manage: DeviceManage, _: AppContext) -> None:

    @mcp.tool(
        description=(
            "采集设备当前状态快照，包括基础属性、联网状态、屏幕状态和电量等信息。"
            " 该工具只做观测，不会修改设备状态。"
            " 多设备场景下应通过 `serial` 指定目标设备。"
        ),
        meta={"hidden": False, "domain": "device", "class": "info", "supports_parallel": True}
    )
    @task_middleware("device_snapshot")
    async def device_snapshot(
        serial: SerialArg = None
    ) -> CallToolResult:

        device = await manage.resolve_fresh(serial)
        raw = await device.device_snapshot()

        return build_tool_result(tool="device_snapshot", args={}, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "截取设备当前屏幕并返回本地附件路径。"
            " 提供 `local` 时会作为保存目录或基准路径使用。"
            " 设备不可用、截图失败或目标路径不可写时调用会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "info", "supports_parallel": True}
    )
    @task_middleware("screenshot")
    async def screenshot(
        local: ScreenshotLocalArg = None,
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "local" : local
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.screenshot(**args)

        return build_tool_result(tool="screenshot", args=args, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "列出设备上已安装的包名。"
            " `keyword` 为空时按 `scope` 返回整类包，非空时做大小写不敏感的包含过滤。"
            " `scope` 只影响候选范围，不会检查应用当前运行状态。"
        ),
        meta={"hidden": False, "domain": "device", "class": "info", "supports_parallel": True}
    )
    @task_middleware("grep_packages")
    async def grep_packages(
        keyword: PackageKeywordArg = None,
        scope: PackageScopeArg = "user",
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "keyword" : keyword,
            "scope"   : scope
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.grep_packages(**args)

        return build_tool_result(tool="grep_packages", args=args, raw=raw, target=device.serial)


if __name__ == '__main__':
    pass
