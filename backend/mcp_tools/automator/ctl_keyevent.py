#   ____ _____ _       _  __                               _
#  / ___|_   _| |     | |/ /___ _   _  _____   _____ _ __ | |_
# | |     | | | |     | ' // _ \ | | |/ _ \ \ / / _ \ '_ \| __|
# | |___  | | | |___  | . \  __/ |_| |  __/\ V /  __/ | | | |_
#  \____| |_| |_____| |_|\_\___|\__, |\___| \_/ \___|_| |_|\__|
#                               |___/
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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("go_home")
    async def go_home(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: go_home
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=3（HOME）
        """

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=3, **a)

        return await broadcast(
            tool="go_home",
            args={"longpress": longpress, "matrix": matrix},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("go_back")
    async def go_back(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: go_back
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)   # serial->key_event kwargs（可覆盖 longpress 等）
        R: CTR
        N:
          - keycode=4（BACK）
          - matrix 存在时：仅执行 matrix 中列出的设备；kwargs 透传给 key_event；其他设备返回 skipped
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("open_recents")
    async def open_recents(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: open_recents
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=187（RECENTS）
          - 系统导航入口（禁止用 click/tap 模拟）
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("open_menu")
    async def open_menu(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: open_menu
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=82（MENU）
          - 系统级入口（禁止用 click/tap 模拟）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=82, **a)

        return await broadcast(
            tool="open_menu",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("press_power")
    async def press_power(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: press_power
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=26（POWER），系统级 key_event（禁止 click/tap）
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("press_enter")
    async def press_enter(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: press_enter
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=66（ENTER），系统级 key_event（禁止 click/tap）
          - 依赖输入焦点（无焦点时可能无效）
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("press_tab")
    async def press_tab(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: press_tab
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=61（TAB），系统级 key_event（禁止 click/tap）
          - 依赖页面存在可聚焦控件
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=61, **a)

        return await broadcast(
            tool="press_tab",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("press_delete")
    async def press_delete(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: press_delete
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=67（DEL），系统级 key_event（禁止 click/tap）
          - 依赖输入焦点（无焦点时可能无效）
        """

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

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("press_space")
    async def press_space(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: press_space
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=62（SPACE），系统级 key_event（禁止 click/tap）
          - 依赖输入焦点（无焦点时可能无效）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=62, **a)

        return await broadcast(
            tool="press_space",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("press_escape")
    async def press_escape(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: press_escape
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=111（ESC），系统级 key_event（禁止 click/tap）
          - 部分应用可能等价“取消/返回”
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=111, **a)

        return await broadcast(
            tool="press_escape",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("press_voice_assist")
    async def press_voice_assist(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: press_voice_assist
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=231（VOICE_ASSIST），系统级入口（禁止 click/tap）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=231, **a)

        return await broadcast(
            tool="press_voice_assist",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("volume_up")
    async def volume_up(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: volume_up
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=24（VOLUME_UP），系统级 key_event（禁止 click/tap）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=24, **a)

        return await broadcast(
            tool="volume_up",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("volume_down")
    async def volume_down(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: volume_down
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=25（VOLUME_DOWN），系统级 key_event（禁止 click/tap）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=25, **a)

        return await broadcast(
            tool="volume_down",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("volume_mute")
    async def volume_mute(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: volume_mute
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=164（MUTE），系统级 key_event（禁止 click/tap）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=164, **a)

        return await broadcast(
            tool="volume_mute",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("media_pause")
    async def media_pause(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: media_pause
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=85（MEDIA_PLAY_PAUSE），系统级 key_event（禁止 click/tap）
          - 不依赖当前应用 UI（走系统媒体会话）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=85, **a)

        return await broadcast(
            tool="media_pause",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("media_next")
    async def media_next(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: media_next
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=87（MEDIA_NEXT），系统级 key_event（禁止 click/tap）
          - 不依赖当前应用 UI（走系统媒体会话）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=87, **a)

        return await broadcast(
            tool="media_next",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "keyevent"})
    @task_middleware("media_previous")
    async def media_previous(
        longpress: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: keyevent
        A: media_previous
        P:
          longpress: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - keycode=88（MEDIA_PREVIOUS），系统级 key_event（禁止 click/tap）
          - 不依赖当前应用 UI（走系统媒体会话）
        """

        args = {
            "longpress" : longpress
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.key_event(keycode=88, **a)

        return await broadcast(
            tool="media_previous",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
