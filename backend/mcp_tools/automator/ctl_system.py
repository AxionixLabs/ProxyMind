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
    @task_middleware("grep_packages_mm")
    async def grep_packages_mm(
        keyword: typing.Optional[str] = None,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: grep_packages_mm
        P:
          keyword: str?=None
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 过滤/列出设备已安装包名：keyword 为空则列出全部；非空则按关键字匹配
          - 基于 `pm list packages | grep -i <keyword>`（设备侧 grep）
        """

        args = {
            "keyword" : keyword
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.grep_packages_mm(**a)

        return await broadcast(
            tool="grep_packages_mm",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

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
    @task_middleware("open_settings")
    async def open_settings(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: open_settings
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 系统级 UI 操作（禁止用 click/tap 模拟）
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.open_settings()

        return await broadcast(
            tool="open_settings",
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
    @task_middleware("screen_on")
    async def screen_on(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
         D: device
         C: system
         A: screen_on
         P:
           matrix: overrides? (serial->args)
         R: CTR
         N:
           - 幂等：已亮则 no-op；仅熄屏时发送 POWER 点亮
         """

        async def call(device: Device, *_) -> typing.Any:
            return await device.screen_set(True)

        return await broadcast(
            tool="screen_on",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("screen_off")
    async def screen_off(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: screen_off
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 幂等：已锁屏则 no-op；仅亮屏时发送 POWER 熄屏
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.screen_set(False)

        return await broadcast(
            tool="screen_off",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("bluetooth_on")
    async def bluetooth_on(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: bluetooth_on
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过 `adb shell svc bluetooth enable`
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.bluetooth_set("enable")

        return await broadcast(
            tool="bluetooth_on",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("bluetooth_off")
    async def bluetooth_off(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: bluetooth_off
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过 `adb shell svc bluetooth disable`
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.bluetooth_set("disable")

        return await broadcast(
            tool="bluetooth_off",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("wifi_on")
    async def wifi_on(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: wifi_on
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过 `adb shell svc wifi enable`
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.wifi_set("enable")

        return await broadcast(
            tool="wifi_on",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("wifi_off")
    async def wifi_off(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: wifi_off
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过 `adb shell svc wifi disable`
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.wifi_set("disable")

        return await broadcast(
            tool="wifi_off",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("data_on")
    async def data_on(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: data_on
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过 `adb shell svc data enable`
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.data_set("enable")

        return await broadcast(
            tool="data_on",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "system"})
    @task_middleware("data_off")
    async def data_off(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: system
        A: data_off
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 通过 `adb shell svc data disable`
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.data_set("disable")

        return await broadcast(
            tool="data_off",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
