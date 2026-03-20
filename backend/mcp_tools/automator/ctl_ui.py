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
    @task_middleware("scroll")
    async def scroll(
        direction: typing.Literal["up", "down", "left", "right"],
        x: int,
        y: int,
        duration: int = 300,
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll
        P:
          direction: oneof(up|down|left|right)
          x: int
          y: int
          duration: int=300   # ms
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 在给定坐标附近执行一次滚动手势。
          - direction 表示内容移动方向，不是手指滑动方向；内部会自动换算成对应手势轨迹。
          - 是否真的发生滚动，取决于当前位置是否存在可滚动容器。
        """

        args = {
            "direction" : direction,
            "x"         : x,
            "y"         : y,
            "duration"  : duration
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.scroll(**a)

        return await broadcast(
            tool="scroll",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(meta={"hidden": False, "domain": "device", "class": "ui"})
    @task_middleware("scroll_to_edge")
    async def scroll_to_edge(
        edge: typing.Literal["top", "bottom"],
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: scroll_to_edge
        P:
          edge: oneof(top|bottom)
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 按指定方向反复滚动，直到到达顶部或底部边界。
          - 内部会通过页面稳定性判断是否已经无法继续滚动。
        """

        args = {
            "edge" : edge
        }

        async def call(device: Device, a: dict) -> dict:
            job_id = await idle.job_begin(f"ui.scroll_to_edge.{a.get('edge')}", args=a)
            try:
                return await device.scroll_to_edge(**a)
            finally:
                await idle.job_final(job_id)

        return await broadcast(
            tool="scroll_to_edge",
            args=args,
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
          - 持续滚动当前页面，直到目标元素出现在视口内，或达到 timeout / max_swipes 上限。
          - 命中后返回节点摘要；should_click=True 时会继续点击该节点中心点。
          - match 和 ignore_case 仅对字符串类定位生效，bbox 走坐标匹配。
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
            return await device.scroll_into_view(**a)

        return await broadcast(
            tool="scroll_into_view",
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
          - 在当前页面查找第一个匹配节点，并点击其中心点。
          - 仅查找当前可见层级，不会自动滚动页面。
          - match 和 ignore_case 仅对字符串类定位生效，bbox 走坐标匹配。
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
          - 在绝对屏幕坐标 `(x, y)` 执行双击。
          - 内部实现是两次连续 tap，中间存在一个很短的固定间隔。
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
          - 向当前已有焦点的输入框注入文本。
          - 该工具不会主动帮你选中输入框，调用前应先把焦点放到目标输入控件。
          - 依赖 AdbIME 可用；输入法未安装、未启用或系统限制时会失败。
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
          - 清空当前已有焦点的输入框内容。
          - 依赖可编辑焦点和 AdbIME；前置条件不满足时可能无效果。
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
          - 读取设备当前前台焦点信息。
          - 优先返回 package 和 activity；系统无法完整解析时 activity 可能为空。
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
          view: oneof(interactive|credible|all)="all"
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - Dump 当前页面层级并解析为 Widget 列表。
          - view=interactive 仅返回可交互控件；view=credible 仅返回具备可识别文本或标识的控件；view=all 返回全部解析结果。
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
          - 在当前页面中查找第一个匹配控件，并返回该控件的摘要信息。
          - 仅基于当前一次层级快照查找，不会自动滚动页面。
          - xpath 当前不作为稳定能力使用；更适合优先使用 id、desc、text 或 bbox。
          - match 和 ignore_case 仅对字符串类定位生效，bbox 走坐标匹配。
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
          - 对给定 locator 执行一次定位诊断，并返回可用于排障的诊断结果。
          - 结果可能包含截图、页面层级、候选节点和命中情况。
          - should_click=True 时若成功命中会尝试点击；wait>0 时会在点击前额外等待。
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
    @task_middleware("wait_element")
    async def wait_element(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 10.0,
        state: typing.Literal["exists", "gone"] = "exists",
        matrix: typing.Optional[dict[str, dict[str, typing.Any]]] = None
    ) -> CallToolResult:
        """
        D: device
        C: ui
        A: wait_element
        P:
          by: oneof(id|desc|text|bbox|xpath)
          value: str|list
          match: oneof(eq|contains|regex)="eq"
          ignore_case: bool=False
          timeout: float=10.0
          state: oneof(exists|gone)="exists"
          matrix: overrides? (serial->args)
        R: CTR
        N:
          - 按固定轮询间隔等待元素出现或消失。
          - timeout 到期后仍未满足目标状态则返回超时结果。
          - match 和 ignore_case 仅对字符串类定位生效，bbox 走坐标匹配。
        """

        args = {
            "by"          : by,
            "value"       : value,
            "match"       : match,
            "ignore_case" : ignore_case,
            "timeout"     : timeout,
            "state"       : state
        }

        async def call(device: Device, a: dict) -> typing.Any:
            return await device.wait_element(**a)

        return await broadcast(
            tool="wait_element",
            args=args,
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )


if __name__ == '__main__':
    pass
