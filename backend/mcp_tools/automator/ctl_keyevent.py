# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_tools.automator.schemas.schema_keyevent import LongPressArg
from backend.mcp_tools.shared import MatrixArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, manage: DeviceManage, ctx: AppContext) -> None:

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
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=3, **a)

        return await broadcast(
            tool="go_home",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

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
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=4, **a)

        return await broadcast(
            tool="go_back",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "发送 RECENTS 键事件（keycode=187）。"
            " 该工具用于打开系统最近任务视图，不依赖页面元素。"
            " 是否支持长按效果取决于设备和系统实现。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("open_recents")
    async def open_recents(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=187, **a)

        return await broadcast(
            tool="open_recents",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "发送 POWER 键事件（keycode=26）。"
            " 该工具常用于亮灭屏或触发系统电源键行为。"
            " 实际效果取决于当前锁屏状态、系统策略和设备实现。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("press_power")
    async def press_power(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=26, **a)

        return await broadcast(
            tool="press_power",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "发送 ENTER 键事件（keycode=66）。"
            " 该工具常用于输入框确认、表单提交或软键盘确认。"
            " 无输入焦点或前台不响应该键时可能无效果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("press_enter")
    async def press_enter(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=66, **a)

        return await broadcast(
            tool="press_enter",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "发送 DELETE 键事件（keycode=67）。"
            " 该工具主要用于删除当前输入焦点附近的文本内容。"
            " 无输入焦点或前台不接受键盘输入时可能无效果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("press_delete")
    async def press_delete(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=67, **a)

        return await broadcast(
            tool="press_delete",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
