#  _   _ ___   ___       _                      _   _
# | | | |_ _| |_ _|_ __ | |_ ___ _ __ __ _  ___| |_(_) ___  _ __
# | | | || |   | || '_ \| __/ _ \ '__/ _` |/ __| __| |/ _ \| '_ \
# | |_| || |   | || | | | ||  __/ | | (_| | (__| |_| | (_) | | | |
#  \___/|___| |___|_| |_|\__\___|_|  \__,_|\___|\__|_|\___/|_| |_|
#

import typing
import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("swipe_up")
    async def swipe_up(x: int, y: int, duration: int = 300) -> typing.Any:
        """Class: ui; Action: 上方向滑动; Args: x,y(int), duration(ms)=300; Use: 页面向上滚动/翻页; Return: list[device_result]; Notes: semantic swipe."""
        device_list = await manage.refresh()

        return await asyncio.gather(
            *(device.swipe_direction("up", x, y, duration)
              for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("swipe_down")
    async def swipe_down(x: int, y: int, duration: int = 300) -> typing.Any:
        """Class: ui; Action: 下方向滑动; Args: x,y(int), duration(ms)=300; Use: 页面向下滚动/返回; Return: list[device_result]; Notes: semantic swipe."""
        device_list = await manage.refresh()

        return await asyncio.gather(
            *(device.swipe_direction("down", x, y, duration)
              for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("swipe_left")
    async def swipe_left(x: int, y: int, duration: int = 300) -> typing.Any:
        """Class: ui; Action: 左方向滑动; Args: x,y(int), duration(ms)=300; Use: 左翻页/轮播切换; Return: list[device_result]; Notes: semantic swipe."""
        device_list = await manage.refresh()

        return await asyncio.gather(
            *(device.swipe_direction("left", x, y, duration)
              for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("swipe_right")
    async def swipe_right(x: int, y: int, duration: int = 300) -> typing.Any:
        """Class: ui; Action: 右方向滑动; Args: x,y(int), duration(ms)=300; Use: 右翻页/进入下一屏; Return: list[device_result]; Notes: semantic swipe."""
        device_list = await manage.refresh()

        return await asyncio.gather(
            *(device.swipe_direction("right", x, y, duration)
              for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("swipe")
    async def swipe(x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> typing.Any:
        """Class: ui; Action: 坐标滑动; Args: x1,y1,x2,y2(int), duration(ms)=300; Use: 滚动/翻页/拖拽; Return: list[device_result]; Notes: absolute coords."""
        device_list = await manage.refresh()

        logger.info(f"Swipe {x1} {y1} {x2} {y2} {duration}")
        return await asyncio.gather(
            *(device.swipe(x1, y1, x2, y2, duration) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("tap")
    async def tap(x: int, y: int) -> typing.Any:
        """Class: ui; Action: 坐标点击; Args: x(int), y(int); Use: 无法定位控件时兜底; Return: list[device_result]; Notes: absolute coords."""
        device_list = await manage.refresh()

        logger.info(f"Tap {x} {y}")
        return await asyncio.gather(
            *(device.tap(x, y) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("click")
    async def click(by: typing.Literal["text", "id", "desc"], value: str) -> typing.Any:
        """Class: ui; Action: 精确属性定位点击; Args: by(text|id|desc), value(str exact); Use: 优先用于可定位控件; Return: list[device_result]; Notes: no fuzzy, not found => no-op per-device."""
        device_list = await manage.refresh()

        logger.info(f"Click by {by} value={value}")
        return await asyncio.gather(
            *(device.click(by, value) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("double_click")
    async def double_click(x: int, y: int) -> typing.Any:
        """Class: ui; Action: 双击坐标; Args: x(int), y(int); Use: 触发双击手势; Return: list[device_result]; Notes: adb input tap x y; sleep 0.08; input tap x y."""
        device_list = await manage.refresh()

        logger.info(f"DoubleClick {x} {y}")
        return await asyncio.gather(
            *(device.double_click(x, y) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("send_keys")
    async def send_keys(text: str) -> typing.Any:
        """Class: ui; Action: 输入文本到当前焦点; Args: text(str); Use: 输入框已聚焦时输入; Return: list[device_result]; Notes: 不负责定位/点击, 无焦点可能失败."""
        device_list = await manage.refresh()

        logger.info(f"Send keys {text}")
        return await asyncio.gather(
            *(device.send_keys(text) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("current_package")
    async def current_package() -> typing.Any:
        """Class: ui; Action: 获取当前前台应用包名(dumpsys window | grep mCurrentFocus); Args: none; Use: 判断前台应用/断言跳转结果/做条件分支; Return: list[device_result]; Notes: 优先从 package/activity 提取 package，失败则退化从 u0 com.xxx 提取。"""
        device_list = await manage.refresh()

        logger.info("Current package")
        return await asyncio.gather(
            *(device.current_package() for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("current_activity")
    async def current_activity() -> typing.Any:
        """Class: ui; Action: 获取当前前台应用界面名Activity(mCurrentFocus); Args: none; Use: 判断当前所在应用/页面(包名或package/activity); Return: list[device_result]; Notes: 优先从 package/activity 提取 activity，失败则退化从 u0 com.xxx 提取。"""
        device_list = await manage.refresh()

        logger.info("Current activity")
        return await asyncio.gather(
            *(device.current_activity() for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("current_xml")
    async def current_xml() -> typing.Any:
        """Class: ui; Action: 导出当前 UI 层级XML(uiautomator dump --compressed + cat); Args: none; Use: 调试/校验控件树/辅助定位; Return: list[device_result]; Notes: dump到/tmp后轮询读取(最多5次)，检测到<hierarchy才返回。"""
        device_list = await manage.refresh()

        logger.info("Current XML")
        return await asyncio.gather(
            *(device.current_xml() for device in device_list), return_exceptions=True
        )


if __name__ == '__main__':
    pass
