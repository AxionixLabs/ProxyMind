# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from pydantic import Field
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext
from backend.utilities.broadcast import broadcast


RefreshTtlArg = typing.Annotated[
    float,
    Field(description="设备列表缓存复用窗口，单位秒。"),
]


def bind(mcp: FastMCP, manage: DeviceManage, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "刷新当前可用设备列表。"
            " `ttl_sec` 窗口内会优先复用缓存，超过后才重新扫描 adb。"
            " 适合在批量执行前同步一次在线设备视图。"
        ),
        meta={"hidden": False, "domain": "device", "class": "tool"}
    )
    @task_middleware("refresh")
    async def refresh(ttl_sec: RefreshTtlArg = 1.0) -> CallToolResult:
        args = {
            "ttl_sec" : ttl_sec
        }

        async def call(*_) -> dict:
            return await manage.refresh_summary(ttl_sec)

        return await broadcast(
            tool="refresh",
            args=args,
            target_list=[None],
            call=call,
            overrides=None
        )


if __name__ == '__main__':
    pass
