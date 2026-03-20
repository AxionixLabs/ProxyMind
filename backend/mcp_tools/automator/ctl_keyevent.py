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
          - 发送 HOME 键事件（keycode=3）。
          - 用于回到系统桌面；是否触发长按效果取决于设备和系统实现。
        """

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
          - 发送 BACK 键事件（keycode=4）。
          - 常用于返回上一级页面、关闭弹窗或退出当前编辑态。
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
          - 发送 RECENTS 键事件（keycode=187）。
          - 用于打开系统最近任务视图，不依赖页面元素。
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
          - 发送 MENU 键事件（keycode=82）。
          - 仅对仍响应物理或系统菜单键的应用或页面有效。
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
          - 发送 POWER 键事件（keycode=26）。
          - 常用于亮灭屏或触发系统电源键行为。
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
          - 发送 ENTER 键事件（keycode=66）。
          - 在输入框、表单或软键盘确认场景中更常见；无输入焦点时可能无效果。
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
          - 发送 TAB 键事件（keycode=61）。
          - 依赖页面存在可聚焦控件，常用于焦点切换。
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
          - 发送 DELETE 键事件（keycode=67）。
          - 主要用于删除当前输入焦点前后的文本内容；无输入焦点时可能无效果。
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
          - 发送 SPACE 键事件（keycode=62）。
          - 主要用于文本输入场景；无输入焦点时可能无效果。
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
          - 发送 ESCAPE 键事件（keycode=111）。
          - 在部分应用中可能表现为取消、关闭弹窗或返回。
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
          - 发送 VOICE_ASSIST 键事件（keycode=231）。
          - 是否有实际效果取决于系统是否配置了语音助手入口。
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
          - 发送 VOLUME_UP 键事件（keycode=24）。
          - 会直接影响系统音量，不依赖当前页面元素。
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
          - 发送 VOLUME_DOWN 键事件（keycode=25）。
          - 会直接影响系统音量，不依赖当前页面元素。
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
          - 发送 MUTE 键事件（keycode=164）。
          - 是否生效取决于设备对该键值的支持情况。
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
          - 发送 MEDIA_PLAY_PAUSE 键事件（keycode=85）。
          - 面向系统媒体会话，不依赖当前页面是否存在播放按钮。
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
          - 发送 MEDIA_NEXT 键事件（keycode=87）。
          - 面向系统媒体会话，不依赖当前页面是否存在下一曲按钮。
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
          - 发送 MEDIA_PREVIOUS 键事件（keycode=88）。
          - 面向系统媒体会话，不依赖当前页面是否存在上一曲按钮。
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
