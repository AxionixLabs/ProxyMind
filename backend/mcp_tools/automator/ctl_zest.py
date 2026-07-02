# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_tools.automator.schemas.schema_zest import RefreshTtlArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext
from backend.utilities.tool_result import build_tool_result


def bind(mcp: FastMCP, manage: DeviceManage, _: AppContext) -> None:

    @mcp.tool(
        description=(
            "刷新当前可用设备列表。"
            " `ttl_sec` 窗口内会优先复用缓存，超过后才重新扫描 adb。"
            " 适合在批量执行前同步一次在线设备视图。"
        ),
        meta={"hidden": False, "domain": "device", "class": "tool"}
    )
    @task_middleware("refresh")
    async def refresh(
        ttl_sec: RefreshTtlArg = 1.0
    ) -> CallToolResult:

        args = {
            "ttl_sec" : ttl_sec
        }

        raw = await manage.refresh_summary(ttl_sec)
        return build_tool_result(tool="refresh", args=args, raw=raw)


if __name__ == '__main__':
    pass
