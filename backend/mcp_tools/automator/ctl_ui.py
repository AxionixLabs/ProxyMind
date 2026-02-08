#   ____ _____ _       _   _ ___
#  / ___|_   _| |     | | | |_ _|
# | |     | | | |     | | | || |
# | |___  | | | |___  | |_| || |
#  \____| |_| |_____|  \___/|___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import Idle
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle) -> None:

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("scroll_up")
    async def scroll_up(
        x: int,
        y: int,
        duration: int = 300,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll_up
        P:
          x: int
          y: int
          duration: int=300   # ms
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 语义是“内容向上滚动”（不是手指方向）；内部会反转为 adb 手指轨迹
          - 具体效果依赖目标控件是否可滚动
        """

        args = {
            "x"        : x,
            "y"        : y,
            "duration" : duration
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.scroll_direction(direction="up", **a)

        return await broadcast(
            tool="scroll_up",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("scroll_down")
    async def scroll_down(
        x: int,
        y: int,
        duration: int = 300,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll_down
        P:
          x: int
          y: int
          duration: int=300   # ms
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 语义是“内容向下滚动”（不是手指方向）；内部会反转为 adb 手指轨迹
          - 具体效果依赖目标控件是否可滚动
        """

        args = {
            "x"        : x,
            "y"        : y,
            "duration" : duration
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.scroll_direction(direction="down", **a)

        return await broadcast(
            tool="scroll_down",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("scroll_left")
    async def scroll_left(
        x: int,
        y: int,
        duration: int = 300,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll_left
        P:
          x: int
          y: int
          duration: int=300   # ms
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 语义是“内容向左滚动”（不是手指方向）；内部会反转为 adb 手指轨迹
          - 具体效果依赖目标控件是否支持横向滚动
        """

        args = {
            "x"        : x,
            "y"        : y,
            "duration" : duration
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.scroll_direction(direction="left", **a)

        return await broadcast(
            tool="scroll_left",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("scroll_right")
    async def scroll_right(
        x: int,
        y: int,
        duration: int = 300,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll_right
        P:
          x: int
          y: int
          duration: int=300   # ms
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 语义是“内容向右滚动”（不是手指方向）；内部会反转为 adb 手指轨迹
          - 具体效果依赖目标控件是否支持横向滚动
        """

        args = {
            "x"        : x,
            "y"        : y,
            "duration" : duration
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.scroll_direction(direction="right", **a)

        return await broadcast(
            tool="scroll_right",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("scroll_to_top")
    async def scroll_to_top(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll_to_top
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 基于 scroll_to_edge('top')：循环滚动，直到到达边界或判定无变化
        """

        async def call(device: Device, a: dict) -> dict:
            job_id = await idle.job_begin("ui.scroll_to_top", args=a)
            try:
                return await device.scroll_to_top()
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="scroll_to_top",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("scroll_to_bottom")
    async def scroll_to_bottom(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll_to_bottom
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 基于 scroll_to_edge('bottom')：循环滚动，直到到达边界或判定无变化
        """

        async def call(device: Device, a: dict) -> dict:
            job_id = await idle.job_begin("ui.scroll_to_bottom", args=a)
            try:
                return await device.scroll_to_bottom()
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="scroll_to_bottom",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("swipe")
    async def swipe(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: int = 300,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: swipe
        P:
          x1: int
          y1: int
          x2: int
          y2: int
          duration: int=300   # ms
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - absolute coordinates（px），效果依赖控件/页面手势响应
        """

        args = {
            "x1"       : x1,
            "y1"       : y1,
            "x2"       : x2,
            "y2"       : y2,
            "duration" : duration
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.swipe(**a)

        return await broadcast(
            tool="swipe",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("tap")
    async def tap(
        x: int,
        y: int,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: tap
        P:
          x: int
          y: int
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - absolute coordinates（px）
        """

        args = {
            "x" : x,
            "y" : y
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.tap(**a)

        return await broadcast(
            tool="tap",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("click")
    async def click(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: click
        P:
          by: oneof(id|desc|text|bbox|xpath)
          value: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 精确匹配（no fuzzy）
          - 未找到控件：按设备返回未命中/无动作
        """

        args = {
            "by"    : by,
            "value" : value
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.click(**a)

        return await broadcast(
            tool="click",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("double_click")
    async def double_click(
        x: int,
        y: int,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: double_click
        P:
          x: int
          y: int
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - absolute coordinates（px）
          - 内部实现：tap -> sleep(0.08) -> tap（具体间隔以实现为准）
        """

        args = {
            "x" : x,
            "y" : y
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.double_click(**a)

        return await broadcast(
            tool="double_click",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("send_keys")
    async def send_keys(
        text: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: send_keys
        P:
          text: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 依赖当前存在可编辑焦点（需你先确保焦点在目标输入框）
          - 可能因输入法不可用/AdbIME 未启用/权限受限而失败
        """

        args = {
            "text" : text
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.send_keys(**a)

        return await broadcast(
            tool="send_keys",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("send_keys_fallback")
    async def send_keys_fallback(
        text: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: send_keys_fallback
        P:
          text: str
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 降级输入：直接使用 `adb shell input text`
          - 依赖当前存在可编辑焦点（需先确保焦点在目标输入框）
          - 对中文/emoji/特殊字符兼容性依设备/ROM 而异；空格会转换为 `%s`
        """

        args = {"text": text}

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.send_keys_fallback(**a)

        return await broadcast(
            tool="send_keys_fallback",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("clear_text")
    async def clear_text(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: clear_text
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 等价 `adb shell am broadcast -a ADB_CLEAR_TEXT`
          - 依赖可编辑焦点/AdbIME 安装与启用/权限；不满足时可能无效果
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.clear_text()

        return await broadcast(
            tool="clear_text",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("current_focus")
    async def current_focus(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: current_focus
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 优先提取 package/activity；失败则退化提取 package（activity 可能为空）
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.current_focus()

        return await broadcast(
            tool="current_focus",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("current_xml")
    async def current_xml(
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: current_xml
        P:
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - uiautomator dump 到 /tmp 后轮询读取（最多 5 次），检测到 `<hierarchy` 才返回
        """

        async def call(device: Device, *_) -> typing.Any:
            return await device.current_xml()

        return await broadcast(
            tool="current_xml",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("wait_exists")
    async def wait_exists(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        timeout: float = 10.0,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: wait_exists
        P:
          by: oneof(id|desc|text|bbox|xpath)
          value: str|list
          timeout: float=10.0
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 复用 wait_element(mode='exists')：轮询 find_node，命中=>True，超时=>False
        """

        args = {
            "by"      : by,
            "value"   : value,
            "timeout" : timeout
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.wait_element(**a, mode="exists")

        return await broadcast(
            tool="wait_exists",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("wait_gone")
    async def wait_gone(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        timeout: float = 10.0,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: wait_gone
        P:
          by: oneof(id|desc|text|bbox|xpath)
          value: str|list
          timeout: float=10.0
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 复用 wait_element(mode='gone')：轮询 find_node，消失=>True，超时=>False
        """

        args = {
            "by"      : by,
            "value"   : value,
            "timeout" : timeout
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.wait_element(**a, mode="gone")

        return await broadcast(
            tool="wait_gone",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("find_element")
    async def find_element(
        locator: str,
        should_click: bool = False,
        wait: float = 0.0,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: find_element
        P:
          locator: str
          should_click: bool=False
          wait: float=0.0
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 每台设备输出一份诊断 payload（可含截图/层级/定位结果）
          - wait>0：命中后点击前 sleep(wait)（用于动画/页面稳定）
          - 采集后会清理远端截图临时文件
        """

        args = {
            "locator"      : locator,
            "should_click" : should_click,
            "wait"         : wait
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.find_element(**a)

        return await broadcast(
            tool="find_element",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
