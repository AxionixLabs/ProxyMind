# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_device import Device
from backend.mcp_hub.hub_manage import DeviceManage
from backend.mcp_tools.automator.schemas.schema_ui import (
    CoordArg,
    DurationArg,
    EdgeArg,
    IgnoreCaseArg,
    InputTextArg,
    LocatorArg,
    LocatorByArg,
    LocatorValueArg,
    MatchModeArg,
    MaxSwipesArg,
    ScrollDirectionArg,
    ShouldClickArg,
    TimeoutArg,
    WaitArg,
    WaitStateArg,
    WidgetViewArg
)
from backend.mcp_tools.shared import MatrixArg
from backend.middlewares.mid_task import task_middleware
from backend.utilities.runtime import (
    AppContext, Idle
)
from backend.utilities.broadcast import broadcast


def bind(mcp: FastMCP, manage: DeviceManage, idle: Idle, ctx: AppContext) -> None:

    @mcp.tool(
        description=(
            "在给定坐标附近执行一次滚动手势。"
            " `direction` 表示内容移动方向，不是手指滑动方向；工具内部会自动换算轨迹。"
            " 是否真的发生滚动取决于当前位置是否存在可滚动容器。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("scroll")
    async def scroll(
        direction: ScrollDirectionArg,
        x: CoordArg,
        y: CoordArg,
        duration: DurationArg = 300,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "按指定方向反复滚动，直到到达顶部或底部边界。"
            " 该工具会根据页面稳定性判断是否已经无法继续滚动。"
            " 适合列表或详情页边界探测，不适合精确定位具体控件。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("scroll_to_edge")
    async def scroll_to_edge(
        edge: EdgeArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "持续滚动当前页面，直到目标元素出现在视口内，或达到超时和滑动上限。"
            " 命中后会返回节点摘要；`should_click` 为 true 时会继续点击该节点中心点。"
            " `match` 和 `ignore_case` 仅对字符串类定位生效，`bbox` 走坐标匹配。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("scroll_into_view")
    async def scroll_into_view(
        by: LocatorByArg,
        value: LocatorValueArg,
        match: MatchModeArg = "eq",
        ignore_case: IgnoreCaseArg = False,
        direction: ScrollDirectionArg = "down",
        timeout: TimeoutArg = 12.0,
        max_swipes: MaxSwipesArg = 12,
        should_click: ShouldClickArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "在当前页面查找第一个匹配节点，并点击其中心点。"
            " 该工具只查找当前可见层级，不会自动滚动页面。"
            " `match` 和 `ignore_case` 仅对字符串类定位生效，`bbox` 走坐标匹配。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("click")
    async def click(
        by: LocatorByArg,
        value: LocatorValueArg,
        match: MatchModeArg = "eq",
        ignore_case: IgnoreCaseArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "在绝对屏幕坐标 `(x, y)` 执行双击。"
            " 该工具直接按坐标下发两次连续点击，不会先做元素查找。"
            " 坐标无效、页面遮挡或目标区域不可点击时可能无效果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("double_click")
    async def double_click(
        x: CoordArg,
        y: CoordArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "向当前已有焦点的输入框注入文本。"
            " 该工具不会主动选中输入框，调用前应先把焦点放到目标控件。"
            " 依赖 AdbIME 可用，输入法未安装、未启用或系统限制时会失败。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("input_text")
    async def input_text(
        text: InputTextArg,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "清空当前已有焦点的输入框内容。"
            " 该工具依赖可编辑焦点和 AdbIME，不会主动帮你选中输入框。"
            " 前置条件不满足时可能无效果。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("clear_text")
    async def clear_text(
        matrix: MatrixArg = None
    ) -> CallToolResult:
        async def call(device: Device, *_) -> typing.Any:
            return await device.clear_text()

        return await broadcast(
            tool="clear_text",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "读取设备当前前台焦点信息。"
            " 该工具优先返回 package 和 activity，不会触发任何页面操作。"
            " 系统无法完整解析时 activity 可能为空。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("current_focus")
    async def current_focus(
        matrix: MatrixArg = None
    ) -> CallToolResult:
        async def call(device: Device, *_) -> typing.Any:
            return await device.current_focus()

        return await broadcast(
            tool="current_focus",
            args={},
            target_list=manage.snapshot,
            call=call,
            overrides=matrix
        )

    @mcp.tool(
        description=(
            "Dump 当前页面层级并解析为 Widget 列表。"
            " `view` 用来限制返回范围，可选全部控件、可交互控件或具备可信标识的控件。"
            " 该工具只读取当前层级快照，不会自动滚动或重试。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("current_widgets")
    async def current_widgets(
        view: WidgetViewArg = "all",
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "在当前页面中查找第一个匹配控件，并返回该控件的摘要信息。"
            " 该工具只基于当前一次层级快照查找，不会自动滚动页面。"
            " `xpath` 当前不作为稳定能力使用，优先使用 `id`、`desc`、`text` 或 `bbox`。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("find_element")
    async def find_element(
        by: LocatorByArg,
        value: LocatorValueArg,
        match: MatchModeArg = "eq",
        ignore_case: IgnoreCaseArg = False,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "对给定 locator 执行一次定位诊断，并返回排障所需的诊断结果。"
            " 结果可能包含截图、页面层级、候选节点和命中情况。"
            " `should_click` 为 true 时命中后会尝试点击，`wait` 大于 0 时会在点击前额外等待。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("heal_element")
    async def heal_element(
        locator: LocatorArg,
        should_click: ShouldClickArg = False,
        wait: WaitArg = 0.0,
        matrix: MatrixArg = None
    ) -> CallToolResult:
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

    @mcp.tool(
        description=(
            "按固定轮询间隔等待元素出现或消失。"
            " `state` 用来指定等待目标是 `exists` 还是 `gone`。"
            " 超时后仍未满足目标状态会返回超时结果，且不会自动滚动页面。"
        ),
        meta={"hidden": False, "domain": "device", "class": "ui"}
    )
    @task_middleware("wait_element")
    async def wait_element(
        by: LocatorByArg,
        value: LocatorValueArg,
        match: MatchModeArg = "eq",
        ignore_case: IgnoreCaseArg = False,
        timeout: TimeoutArg = 10.0,
        state: WaitStateArg = "exists",
        matrix: MatrixArg = None
    ) -> CallToolResult:
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
