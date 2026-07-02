# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.utilities.tool_result import build_tool_result
from backend.mcp_tools.automator.schemas.schema_system import (
    RebootModeArg,
    ToggleArg,
    WaitReconnectArg,
    WaitTimeoutArg
)
from backend.mcp_tools.shared import SerialArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import AppContext


def bind(mcp: FastMCP, manage: DeviceManage, _: AppContext) -> None:

    @mcp.tool(
        description=(
            "展开系统通知栏。"
            " 这是系统级入口操作，不依赖页面元素，也不需要通过坐标点击模拟。"
            " 适合先打开通知视图，再配合 UI 工具继续操作。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("open_notification")
    async def open_notification(
        serial: SerialArg = None
    ) -> CallToolResult:

        device = await manage.resolve_fresh(serial)
        raw = await device.open_notification()

        return build_tool_result(tool="open_notification", args={}, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "展开系统快捷设置面板。"
            " 这是系统级入口操作，不依赖页面元素，也不需要通过坐标点击模拟。"
            " 适合先打开快捷设置，再配合 UI 工具定位具体开关。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("open_quick_settings")
    async def open_quick_settings(
        serial: SerialArg = None
    ) -> CallToolResult:

        device = await manage.resolve_fresh(serial)
        raw = await device.open_quick_settings()

        return build_tool_result(tool="open_quick_settings", args={}, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "重启设备，并可选进入 recovery、bootloader 或 edl。"
            " 只有在普通重启且 `wait` 为 true 时才会等待设备重新回到 adb online。"
            " 重启到特殊模式后通常不会回到正常 adb online，不建议开启等待。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("reboot")
    async def reboot(
        mode: RebootModeArg = "",
        wait: WaitReconnectArg = False,
        wait_timeout: WaitTimeoutArg = 120.0,
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "mode"         : mode,
            "wait"         : wait,
            "wait_timeout" : wait_timeout
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.reboot(**args)

        return build_tool_result(tool="reboot", args=args, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "尝试点亮屏幕并执行一次上滑解锁。"
            " 该工具只覆盖无密码的滑动解锁场景。"
            " 不处理 PIN、图案、指纹或人脸等二次认证。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("swipe_unlock")
    async def swipe_unlock(
        serial: SerialArg = None
    ) -> CallToolResult:

        device = await manage.resolve_fresh(serial)
        raw = await device.swipe_unlock()

        return build_tool_result(tool="swipe_unlock", args={}, raw=raw, target=device.serial)

    @mcp.tool(
        description=(
            "把屏幕切换到目标开关状态。"
            " 工具会先检查当前亮灭屏状态，只在状态不一致时才发送 POWER 键。"
            " 它保证的是目标状态收敛，不保证模拟一次原始电源键点击。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("set_screen")
    async def set_screen(
        on: ToggleArg,
        serial: SerialArg = None
    ) -> CallToolResult:

        args = {
            "on" : on
        }

        device = await manage.resolve_fresh(serial)
        raw = await device.set_screen(**args)

        return build_tool_result(tool="set_screen", args=args, raw=raw, target=device.serial)


if __name__ == '__main__':
    pass
