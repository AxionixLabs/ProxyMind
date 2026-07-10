# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.utilities.tool_result import build_tool_result
from backend.mcp_tools.automator.schemas.schema_keyevent import LongPressArg
from backend.mcp_tools.shared import SerialArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext


def bind(mcp: FastMCP, manage: DeviceManage, _: AppContext) -> None:

    @mcp.tool(
        description=(
            "发送 HOME 键事件（keycode=3）。"
            " 该工具用于回到系统桌面，不依赖页面元素。"
            " 是否触发长按效果取决于设备和系统实现。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("go_home")
    async def go_home(
        longpress: LongPressArg = False,
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "longpress" : longpress
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.key_event(keycode=3, **args)

        return build_tool_result(tool="go_home", args=args, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "发送 BACK 键事件（keycode=4）。"
            " 该工具常用于返回上一级页面、关闭弹窗或退出当前编辑态。"
            " 是否表现为普通返回还是长按行为取决于系统和当前前台上下文。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("go_back")
    async def go_back(
        longpress: LongPressArg = False,
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "longpress" : longpress
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.key_event(keycode=4, **args)

        return build_tool_result(tool="go_back", args=args, raw=raw, target=device.serial)


if __name__ == '__main__':
    pass
