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
          - 系统级 UI 操作（禁止用 click/tap 模拟）
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
          - 系统级 UI 操作（禁止用 click/tap 模拟）
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
          - shell-level 组合键（近同时触发）
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
          - 等价 `adb shell ime reset`
          - 可能受系统策略/权限影响而失败
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
          - mode="" 且 wait=True 时：等待设备重新 adb online（超时 wait_timeout）
          - mode!= ""（recovery/bootloader/edl）通常不回到 adb online，不建议 wait=True
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
          - 点亮 + 上滑解锁（不处理密码/指纹/人脸等二次验证）
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
          - 幂等：仅在目标状态不一致时发送 POWER 切换
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
          - 通过 `adb shell svc bluetooth enable|disable`
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
          - 通过 `adb shell svc wifi enable|disable`
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
          - 通过 `adb shell svc data enable|disable`
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
