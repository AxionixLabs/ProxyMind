#  _   _ ___   ___       _                      _   _
# | | | |_ _| |_ _|_ __ | |_ ___ _ __ __ _  ___| |_(_) ___  _ __
# | | | || |   | || '_ \| __/ _ \ '__/ _` |/ __| __| |/ _ \| '_ \
# | |_| || |   | || | | | ||  __/ | | (_| | (__| |_| | (_) | | | |
#  \___/|___| |___|_| |_|\__\___|_|  \__,_|\___|\__|_|\___/|_| |_|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.pipeline import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("swipe_up")
    async def swipe_up(x: int, y: int, duration: int = 300) -> CallToolResult:
        """Class: ui; Action: 上方向滑动; Args: x,y(int), duration(ms)=300; Use: 页面向上滚动/翻页; Return: CallToolResult(text + structuredContent); Notes: semantic swipe."""
        return await broadcast(
            tool="swipe_up",
            args={"x": x, "y": y, "duration": duration},
            target_list=manage.snapshot,
            call=lambda agent: agent.swipe_direction("up", x, y, duration)
        )

    @mcp.tool()
    @task_middleware("swipe_down")
    async def swipe_down(x: int, y: int, duration: int = 300) -> CallToolResult:
        """Class: ui; Action: 下方向滑动; Args: x,y(int), duration(ms)=300; Use: 页面向下滚动/返回; Return: CallToolResult(text + structuredContent); Notes: semantic swipe."""
        return await broadcast(
            tool="swipe_down",
            args={"x": x, "y": y, "duration": duration},
            target_list=manage.snapshot,
            call=lambda agent: agent.swipe_direction("down", x, y, duration)
        )

    @mcp.tool()
    @task_middleware("swipe_left")
    async def swipe_left(x: int, y: int, duration: int = 300) -> CallToolResult:
        """Class: ui; Action: 左方向滑动; Args: x,y(int), duration(ms)=300; Use: 左翻页/轮播切换; Return: CallToolResult(text + structuredContent); Notes: semantic swipe."""
        return await broadcast(
            tool="swipe_left",
            args={"x": x, "y": y, "duration": duration},
            target_list=manage.snapshot,
            call=lambda agent: agent.swipe_direction("left", x, y, duration)
        )

    @mcp.tool()
    @task_middleware("swipe_right")
    async def swipe_right(x: int, y: int, duration: int = 300) -> CallToolResult:
        """Class: ui; Action: 右方向滑动; Args: x,y(int), duration(ms)=300; Use: 右翻页/进入下一屏; Return: CallToolResult(text + structuredContent); Notes: semantic swipe."""
        return await broadcast(
            tool="swipe_right",
            args={"x": x, "y": y, "duration": duration},
            target_list=manage.snapshot,
            call=lambda agent: agent.swipe_direction("right", x, y, duration)
        )

    @mcp.tool()
    @task_middleware("swipe")
    async def swipe(x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> CallToolResult:
        """Class: ui; Action: 坐标滑动; Args: x1,y1,x2,y2(int), duration(ms)=300; Use: 滚动/翻页/拖拽; Return: CallToolResult(text + structuredContent); Notes: absolute coords."""
        return await broadcast(
            tool="swipe",
            args={"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration},
            target_list=manage.snapshot,
            call=lambda agent: agent.swipe(x1, y1, x2, y2, duration)
        )

    @mcp.tool()
    @task_middleware("tap")
    async def tap(x: int, y: int) -> CallToolResult:
        """Class: ui; Action: 坐标点击; Args: x(int), y(int); Use: 无法定位控件时兜底; Return: CallToolResult(text + structuredContent); Notes: absolute coordinates."""
        return await broadcast(
            tool="tap",
            args={"x": x, "y": y},
            target_list=manage.snapshot,
            call=lambda agent: agent.tap(x, y)
        )

    @mcp.tool()
    @task_middleware("click")
    async def click(by: typing.Literal["id", "desc", "text", "bbox", "xpath"], value: str) -> CallToolResult:
        """Class: ui; Action: 精确属性定位点击; Args: by(id|desc|text|bbox|xpath), value(str exact); Use: 优先用于可定位控件; Return: CallToolResult(text + structuredContent); Notes: no fuzzy, not found => no-op per-device."""
        return await broadcast(
            tool="click",
            args={"by": by, "value": value},
            target_list=manage.snapshot,
            call=lambda agent: agent.click(by, value)
        )

    @mcp.tool()
    @task_middleware("double_click")
    async def double_click(x: int, y: int) -> CallToolResult:
        """Class: ui; Action: 双击坐标; Args: x(int), y(int); Use: 触发双击手势; Return: CallToolResult(text + structuredContent); Notes: adb input tap x y; sleep 0.08; input tap x y."""
        return await broadcast(
            tool="double_click",
            args={"x": x, "y": y},
            target_list=manage.snapshot,
            call=lambda agent: agent.double_click(x, y)
        )

    @mcp.tool()
    @task_middleware("send_keys")
    async def send_keys(text: str) -> CallToolResult:
        """Class: ui; Action: 输入文本到当前焦点; Args: text(str); Use: 输入框已聚焦时输入; Return: CallToolResult(text + structuredContent); Notes: 不负责定位/点击, 无焦点可能失败。"""
        return await broadcast(
            tool="send_keys",
            args={"text": text},
            target_list=manage.snapshot,
            call=lambda agent: agent.send_keys(text)
        )

    @mcp.tool()
    @task_middleware("current_package")
    async def current_package() -> CallToolResult:
        """Class: ui; Action: 获取当前前台应用包名(dumpsys window | grep mCurrentFocus); Args: none; Use: 判断前台应用/断言跳转结果/做条件分支; Return: CallToolResult(text + structuredContent); Notes: 优先从 package/activity 提取 package，失败则退化从 u0 com.xxx 提取。"""
        return await broadcast(
            tool="current_package",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.current_package()
        )

    @mcp.tool()
    @task_middleware("current_activity")
    async def current_activity() -> CallToolResult:
        """Class: ui; Action: 获取当前前台应用界面名Activity(mCurrentFocus); Args: none; Use: 判断当前所在应用/页面(包名或package/activity); Return: CallToolResult(text + structuredContent); Notes: 优先从 package/activity 提取 activity，失败则退化从 u0 com.xxx 提取。"""
        return await broadcast(
            tool="current_activity",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.current_activity()
        )

    @mcp.tool()
    @task_middleware("current_xml")
    async def current_xml() -> CallToolResult:
        """Class: ui; Action: 导出当前 UI 层级XML(uiautomator dump --compressed + cat); Args: none; Use: 调试/校验控件树/辅助定位; Return: CallToolResult(text + structuredContent); Notes: dump到/tmp后轮询读取(最多5次)，检测到<hierarchy才返回。"""
        return await broadcast(
            tool="current_xml",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.current_xml()
        )

    @mcp.tool()
    @task_middleware("find_element")
    async def find_element(locator: str, should_click: bool = False, wait: float = 0.0) -> CallToolResult:
        """Class: ui; Action: 元素查找/修复/自愈; Args: locator(str), should_click(bool)=找到后是否期望点击, wait(float)=点击前等待秒数(用于页面/动画稳定); Use: 采集当前页面信息用于元素查找/修复/自愈/诊断; Return: CallToolResult(text + structuredContent); Notes: 每台设备输出一份 payload; wait>0 时每台设备在点击前 sleep(wait); 截图拉取后编码，结束清理远端截图文件。"""
        return await broadcast(
            tool="find_element",
            args={"locator": locator, "should_click": should_click, "wait": wait},
            target_list=manage.snapshot,
            call=lambda agent: agent.find_element(locator, should_click, wait)
        )

    @mcp.tool()
    @task_middleware("wait_exists")
    async def wait_exists(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"], value: str | list, timeout: float = 10.0
    ) -> CallToolResult:
        """Class: ui; Action: 等待元素出现(wait exists); Args: by(str), value(str|list), timeout(float); Use: 页面跳转/动画后等待目标控件出现; Return: CallToolResult(text + structuredContent); Notes: 复用 device.wait_element(mode='exists') 轮询 find_node，命中返回True，超时返回False."""
        return await broadcast(
            tool="wait_exists",
            args={"by": by, "value": value, "timeout": timeout},
            target_list=manage.snapshot,
            call=lambda agent: agent.wait_element(by, value, "exists", timeout)
        )

    @mcp.tool()
    @task_middleware("wait_gone")
    async def wait_gone(
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"], value: str | list, timeout: float = 10.0
    ) -> CallToolResult:
        """Class: ui; Action: 等待元素消失(wait gone); Args: by(str), value(str|list), timeout(float); Use: 等待加载框/弹窗/Toast消失以继续流程; Return: CallToolResult(text + structuredContent); Notes: 复用 device.wait_element(mode='gone') 轮询 find_node，消失返回True，超时返回False."""
        return await broadcast(
            tool="wait_gone",
            args={"by": by, "value": value, "timeout": timeout},
            target_list=manage.snapshot,
            call=lambda agent: agent.wait_element(by, value, "gone", timeout)
        )


if __name__ == '__main__':
    pass
