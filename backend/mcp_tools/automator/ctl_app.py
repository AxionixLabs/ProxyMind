# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_tools.automator.schemas.schema_app import (
    ActivityArg,
    UrlArg
)
from backend.utilities.tool_result import build_tool_result
from backend.mcp_tools.shared import (
    PackageArg,
    SerialArg
)
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext


def bind(mcp: FastMCP, manage: DeviceManage, _: AppContext) -> None:

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
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "url" : url
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.app_deep_link(**args)

        return build_tool_result(tool="app_deep_link", args=args, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "尝试把目标应用带到前台，并返回是否成功进入前台。"
            " 该工具会先检查当前前台；`activity` 非空时会把指定 Activity 作为验收目标。"
            " 未稳定命中时会执行启动并等待前台稳定命中。"
            " 首次拉起失败时会执行一次 force-stop 后重试，因此它适合前台验收场景。"
        ),
        meta={"hidden": False, "domain": "device", "class": "app"}
    )
    @task_middleware("app_foreground")
    async def app_foreground(
        package: PackageArg,
        activity: ActivityArg = None,
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "package"  : package,
            "activity" : activity
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.app_foreground(**args)

        return build_tool_result(tool="app_foreground", args=args, raw=raw, target=device.serial)

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
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "package" : package
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.app_stop(**args)

        return build_tool_result(tool="app_stop", args=args, raw=raw, target=device.serial)

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
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "package" : package
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.app_clear(**args)

        return build_tool_result(tool="app_clear", args=args, raw=raw, target=device.serial)


if __name__ == '__main__':
    pass
