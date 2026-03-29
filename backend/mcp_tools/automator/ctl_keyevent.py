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
from backend.utilities.toolbox import broadcast


LongPressArg = typing.Annotated[
    bool,
    Field(description="是否以长按方式发送该按键事件。"),
]


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

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
            "发送 MENU 键事件（keycode=82）。"
            " 该工具只对仍响应系统或物理菜单键的应用和页面有效。"
            " 在现代应用里可能没有任何效果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("open_menu")
    async def open_menu(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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
            "发送 TAB 键事件（keycode=61）。"
            " 该工具主要用于焦点切换，不会主动创建新的可聚焦控件。"
            " 依赖页面存在可聚焦元素，无焦点链时可能无效果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("press_tab")
    async def press_tab(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 SPACE 键事件（keycode=62）。"
            " 该工具主要用于文本输入场景。"
            " 无输入焦点或前台不接受键盘输入时可能无效果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("press_space")
    async def press_space(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 ESCAPE 键事件（keycode=111）。"
            " 在部分应用中它会表现为取消、关闭弹窗或返回。"
            " 是否有实际效果取决于前台应用是否响应该键值。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("press_escape")
    async def press_escape(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 VOICE_ASSIST 键事件（keycode=231）。"
            " 该工具用于触发系统配置的语音助手入口。"
            " 系统未配置对应能力时可能没有任何效果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("press_voice_assist")
    async def press_voice_assist(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 VOLUME_UP 键事件（keycode=24）。"
            " 该工具会直接影响系统音量，不依赖当前页面元素。"
            " 静音策略、外设接入或系统限制可能影响实际结果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("volume_up")
    async def volume_up(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 VOLUME_DOWN 键事件（keycode=25）。"
            " 该工具会直接影响系统音量，不依赖当前页面元素。"
            " 静音策略、外设接入或系统限制可能影响实际结果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("volume_down")
    async def volume_down(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 MUTE 键事件（keycode=164）。"
            " 该工具用于请求系统静音，不依赖页面元素。"
            " 是否生效取决于设备对该键值的支持情况。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("volume_mute")
    async def volume_mute(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 MEDIA_PLAY_PAUSE 键事件（keycode=85）。"
            " 该工具面向系统媒体会话，不依赖当前页面是否存在播放按钮。"
            " 是否有实际效果取决于系统当前是否存在活跃媒体会话。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("media_pause")
    async def media_pause(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 MEDIA_NEXT 键事件（keycode=87）。"
            " 该工具面向系统媒体会话，不依赖当前页面是否存在下一曲按钮。"
            " 是否有实际效果取决于系统当前是否存在活跃媒体会话。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("media_next")
    async def media_next(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "发送 MEDIA_PREVIOUS 键事件（keycode=88）。"
            " 该工具面向系统媒体会话，不依赖当前页面是否存在上一曲按钮。"
            " 是否有实际效果取决于系统当前是否存在活跃媒体会话。"
        ),
        meta={"hidden": False, "domain": "device", "class": "keyevent"}
    )
    @task_middleware("media_previous")
    async def media_previous(
        longpress: LongPressArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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
