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
            return await device.scroll_up(**a)

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
            return await device.scroll_down(**a)

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
            return await device.scroll_left(**a)

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
            return await device.scroll_right(**a)

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
          - 循环滚动，直到到达边界或判定无变化
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
          - 循环滚动，直到到达边界或判定无变化
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
    @task_middleware("scroll_into_view")
    async def scroll_into_view(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: typing.Union[str, list],
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        direction: typing.Literal["down", "up", "left", "right"] = "down",
        timeout: float = 12.0,
        max_swipes: int = 12,
        should_click: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll_into_view
        P:
          by: oneof(id|desc|text|bbox|xpath)
          value: str|list
          match: oneof(eq|contains|regex)="eq"
          ignore_case: bool=False
          direction: oneof(down|up|left|right)="down"
          timeout: float=12.0
          max_swipes: int=12
          should_click: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 连续滚动直到目标元素可见并返回命中节点信息；超过 max_swipes 或 timeout 则停止
          - should_click=True 时命中后点击元素中心点
          - match/ignore_case 仅对字符串类定位（id/desc/text/xpath）生效
        """

        args = {
            "by"           : by,
            "value"        : value,
            "match"        : match,
            "ignore_case"  : ignore_case,
            "direction"    : direction,
            "timeout"      : timeout,
            "max_swipes"   : max_swipes,
            "should_click" : should_click
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.scroll_element_into_view(**a)

        return await broadcast(
            tool="scroll_into_view",
            args=args,
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
    @task_middleware("click")
    async def click(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: typing.Union[str, list],
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: click
        P:
          by: oneof(id|desc|text|bbox|xpath)
          value: str|list  # bbox=[x1,y1,x2,y2]
          match: oneof(eq|contains|regex)="eq"
          ignore_case: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 按选择器查找节点（支持非精确匹配）并点击其中心点
          - match/ignore_case 仅对字符串类定位（id/desc/text/xpath）生效
        """

        args = {
            "by"          : by,
            "value"       : value,
            "match"       : match,
            "ignore_case" : ignore_case
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
    @task_middleware("input_text")
    async def input_text(
        text: str,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: input_text
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
            return await device.input_text(**a)

        return await broadcast(
            tool="input_text",
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
    @task_middleware("current_widgets")
    async def current_widgets(
        view: typing.Literal["interactive", "credible", "all"] = "all",
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: current_widgets
        P:
          view: oneof(interactive|credible|all)="interactive"
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - Dump 当前页面 XML，并解析为 Widget 语义清单（每行一个控件）
          - view:
              - interactive：仅保留可交互控件（clickable/focusable/scrollable 任一为 True）
              - credible：仅保留可识别控件（id/desc/text 任一非空）
              - all：全量控件
        """

        args = {
            "view" : view
        }

        async def call(device: "Device", a: dict) -> typing.Any:
            return await device.current_widgets(**a)

        return await broadcast(
            tool="current_widgets",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("find_element")
    async def find_element(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: find_element
        P:
          by: oneof(id|desc|text|bbox|xpath)
          value: str|list
          match: oneof(eq|contains|regex)="eq"
          ignore_case: bool=False
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 在当前页面控件列表中查找第一个匹配控件，并返回语义摘要
          - by=xpath：不支持（Android uiautomator dump 非标准 XPath）
          - match:
              - eq：精确匹配
              - contains：子串匹配
              - regex：正则匹配（value 作为 pattern）
          - ignore_case：contains/regex 时可选忽略大小写
          - match/ignore_case 仅对字符串类定位（id/desc/text/xpath）生效
        """

        args = {
            "by"          : by,
            "value"       : value,
            "match"       : match,
            "ignore_case" : ignore_case
        }

        async def call(device: "Device", a: dict) -> typing.Any:
            return await device.find_element(**a)

        return await broadcast(
            tool="find_element",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("heal_element")
    async def heal_element(
        locator: str,
        should_click: bool = False,
        wait: float = 0.0,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: heal_element
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
            return await device.heal_element(**a)

        return await broadcast(
            tool="heal_element",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("wait_exists")
    async def wait_exists(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
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
          match: oneof(eq|contains|regex)="eq"
          ignore_case: bool=False
          timeout: float=10.0
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 轮询查找元素，命中=>True，超时=>False
          - match/ignore_case 仅对字符串类定位（id/desc/text/xpath）生效
        """

        args = {
            "by"          : by,
            "value"       : value,
            "match"       : match,
            "ignore_case" : ignore_case,
            "timeout"     : timeout
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.wait_exists(**a)

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
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
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
          match: oneof(eq|contains|regex)="eq"
          ignore_case: bool=False
          timeout: float=10.0
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 轮询查找元素，消失=>True，超时=>False
          - match/ignore_case 仅对字符串类定位（id/desc/text/xpath）生效
        """

        args = {
            "by"          : by,
            "value"       : value,
            "match"       : match,
            "ignore_case" : ignore_case,
            "timeout"     : timeout
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.wait_gone(**a)

        return await broadcast(
            tool="wait_gone",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
