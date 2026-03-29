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
from backend.utilities.runtime import AppContext
from backend.utilities.broadcast import broadcast


KeyCodeArg = typing.Annotated[
    int,
    Field(description="Android keycode。"),
]
KeyCodeListArg = typing.Annotated[
    list[int],
    Field(description="需要与 `first` 组合触发的附加 keycode 列表。"),
]
RebootModeArg = typing.Annotated[
    typing.Literal["", "recovery", "bootloader", "edl"],
    Field(description="重启目标模式；空字符串表示普通重启。"),
]
WaitReconnectArg = typing.Annotated[
    bool,
    Field(description="普通重启后是否等待设备重新回到 adb online。"),
]
WaitTimeoutArg = typing.Annotated[
    float,
    Field(description="等待设备重新上线的超时时间，单位秒。"),
]
ToggleArg = typing.Annotated[
    bool,
    Field(description="目标开关状态。"),
]


def bind(mcp: FastMCP, manage: DeviceManage, ctx: AppContext) -> None:

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
        matrix: MatrixArg = None
    ) -> CallToolResult:
        async def call(device: Device, *_) -> typing.Any:
            return await device.open_notification()

        return await broadcast(
            tool="open_notification",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

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
        matrix: MatrixArg = None
    ) -> CallToolResult:
        async def call(device: Device, *_) -> typing.Any:
            return await device.open_quick_settings()

        return await broadcast(
            tool="open_quick_settings",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "发送一组组合按键。"
            " `first` 会先进入按下状态，`others` 作为近同时追加触发的按键列表。"
            " 是否被系统或应用识别取决于当前设备、输入上下文和 ROM 行为。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("combo_key")
    async def combo_key(
        first: KeyCodeArg,
        others: KeyCodeListArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "first"  : first,
            "others" : others
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.combo_key(**a)

        return await broadcast(
            tool="combo_key",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "重置设备当前输入法配置。"
            " 该工具常用于输入法状态异常后的恢复，不会主动执行文本输入。"
            " 是否生效取决于系统策略、当前权限和设备 ROM 行为。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("ime_reset")
    async def ime_reset(
        matrix: MatrixArg = None
    ) -> CallToolResult:
        async def call(device: Device, *_) -> typing.Any:
            return await device.ime_reset()

        return await broadcast(
            tool="ime_reset",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

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
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {
            "mode"         : mode,
            "wait"         : wait,
            "wait_timeout" : wait_timeout
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.reboot(**a)

        return await broadcast(
            tool="reboot",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

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
        matrix: MatrixArg = None
    ) -> CallToolResult:
        async def call(device: Device, *_) -> typing.Any:
            return await device.swipe_unlock()

        return await broadcast(
            tool="swipe_unlock",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

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
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {"on": on}

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.set_screen(**a)

        return await broadcast(
            tool="set_screen",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "通过系统 `svc bluetooth` 打开或关闭蓝牙。"
            " 该工具直接操作系统服务，不依赖快捷设置面板。"
            " 是否允许切换取决于设备系统版本、ROM 限制和 adb 权限。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("set_bluetooth")
    async def set_bluetooth(
        enabled: ToggleArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {"enabled": enabled}

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.set_bluetooth(**a)

        return await broadcast(
            tool="set_bluetooth",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "通过系统 `svc wifi` 打开或关闭 Wi-Fi。"
            " 该工具直接操作系统服务，不依赖快捷设置面板。"
            " 是否允许切换取决于设备系统版本、ROM 限制和 adb 权限。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("set_wifi")
    async def set_wifi(
        enabled: ToggleArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {"enabled": enabled}

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.set_wifi(**a)

        return await broadcast(
            tool="set_wifi",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "通过系统 `svc data` 打开或关闭移动数据。"
            " 该工具直接操作系统服务，不依赖运营商设置页面。"
            " 是否允许切换取决于设备系统版本、ROM 限制和 adb 权限。"
        ),
        meta={"hidden": False, "domain": "device", "class": "system"}
    )
    @task_middleware("set_mobile_data")
    async def set_mobile_data(
        enabled: ToggleArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
        args = {"enabled": enabled}

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.set_mobile_data(**a)

        return await broadcast(
            tool="set_mobile_data",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
