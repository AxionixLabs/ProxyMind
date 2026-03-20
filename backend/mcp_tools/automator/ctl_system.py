#   ____ _____ _       ____            _
#  / ___|_   _| |     / ___| _   _ ___| |_ ___ _ __ ___
# | |     | | | |     \___ \| | | / __| __/ _ \ '_ ` _ \
# | |___  | | | |___   ___) | |_| \__ \ ||  __/ | | | | |
#  \____| |_| |_____| |____/ \__, |___/\__\___|_| |_| |_|
#                            |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("open_notification")
    async def open_notification(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: open_notification
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 展开系统通知栏。
          - 这是系统级入口操作，不依赖页面元素，也不需要通过 click/tap 模拟。
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.open_notification()

        return await broadcast(
            tool="open_notification",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("open_quick_settings")
    async def open_quick_settings(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: open_quick_settings
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 展开系统快捷设置面板。
          - 这是系统级入口操作，不依赖页面元素，也不需要通过 click/tap 模拟。
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.open_quick_settings()

        return await broadcast(
            tool="open_quick_settings",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("combo_key")
    async def combo_key(
        first: int,
        others: list[int],
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: combo_key
        P:
          first: int          # keycode，长按
          others: list[int]   # keycode 列表，近同时触发
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 发送一组组合按键。
          - first 作为起始按键，others 作为近同时追加触发的按键列表。
          - 是否被系统或应用识别，取决于当前设备、输入上下文和 ROM 行为。
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("ime_reset")
    async def ime_reset(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: ime_reset
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 重置设备当前输入法配置。
          - 常用于输入法状态异常后的恢复；是否生效取决于系统策略与权限。
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.ime_reset()

        return await broadcast(
            tool="ime_reset",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("reboot")
    async def reboot(
        mode: typing.Literal["", "recovery", "bootloader", "edl"] = "",
        wait: bool = False,
        wait_timeout: float = 120.0,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: reboot
        P:
          mode: oneof(""|recovery|bootloader|edl) = ""
          wait: bool = False
          wait_timeout: float = 120.0
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 重启设备，可选重启到 recovery、bootloader 或 edl。
          - 仅在 mode="" 且 wait=True 时等待设备重新回到 adb online。
          - 若重启到 recovery、bootloader 或 edl，通常不会回到正常 adb online，不建议开启 wait。
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("swipe_unlock")
    async def swipe_unlock(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: swipe_unlock
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 尝试点亮屏幕并执行一次上滑解锁。
          - 只覆盖无密码的滑动解锁场景，不处理 PIN、图案、指纹或人脸等二次认证。
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.swipe_unlock()

        return await broadcast(
            tool="swipe_unlock",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("set_screen")
    async def set_screen(
        on: bool,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: set_screen
        P:
          on: bool
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 把屏幕切换到目标开关状态。
          - 内部先检查当前亮灭屏状态，仅在状态不一致时才发送 POWER 键切换。
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("set_bluetooth")
    async def set_bluetooth(
        enabled: bool,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: set_bluetooth
        P:
          enabled: bool
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过系统 `svc bluetooth` 打开或关闭蓝牙。
          - 是否允许切换取决于设备系统版本、ROM 限制和 adb 权限。
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("set_wifi")
    async def set_wifi(
        enabled: bool,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: set_wifi
        P:
          enabled: bool
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过系统 `svc wifi` 打开或关闭 Wi-Fi。
          - 是否允许切换取决于设备系统版本、ROM 限制和 adb 权限。
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("set_mobile_data")
    async def set_mobile_data(
        enabled: bool,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: set_mobile_data
        P:
          enabled: bool
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过系统 `svc data` 打开或关闭移动数据。
          - 是否允许切换取决于设备系统版本、ROM 限制和 adb 权限。
        """

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
