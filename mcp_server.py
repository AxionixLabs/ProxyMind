import typing
import asyncio
from loguru import logger
from mcp.server import FastMCP
from engine.manage import Manage
from utils import const

mcp = FastMCP(
    name=const.APP_DESC,
    website_url=const.APP_URL,
    host="127.0.0.1",
    port=3333,
    json_response=True
)
mng = Manage("adb")


@mcp.tool()
async def click(by: typing.Literal["text", "resource-id"], value: str) -> typing.Any:
    """
    按指定 UI 属性精确匹配并点击第一个命中的控件。

    参数：
    - by:
        - "text"         → 按控件 text 属性精确匹配
        - "resource-id"  → 按控件 resource-id 精确匹配
        - "content_desc" → 按控件 content-desc 精确匹配
    - value:
        对应属性的匹配值，必须完整匹配，不支持模糊匹配。

    行为：
    - 获取当前 UI 层级 XML（uiautomator dump）
    - 在节点树中查找第一个 node.get(by) == value 的控件
    - 计算控件 bounds 中心点并执行点击
    - 在所有已连接设备上并发执行点击操作
    - 未找到匹配控件的设备不会执行点击

    返回：
    - 各设备点击操作结果列表

    示例：
    click(by="text", value="登录")
    click(by="resource-id", value="com.xx:id/login_btn")
    click(by="content_desc", value="login_button")

    Agent 使用语义：
    当需要对明确 UI 元素执行点击操作时使用。

    约束：
    - 仅支持精确匹配
    - 不支持 contains / 正则 / 模糊匹配
    - UI 文案或 ID 变化将导致匹配失败
    """

    device_list = await mng.refresh()

    logger.info(f"Click by {by} value={value}")
    return await asyncio.gather(
        *(device.click(by, value) for device in device_list)
    )


@mcp.tool()
async def send_keys(text: str) -> typing.Any:
    """
    向当前已聚焦的输入框逐行输入文本。

    功能说明：
    - 仅向当前获得输入焦点的控件输入文本
    - 不进行任何控件定位或点击操作
    - 遇到换行符 \\n 时自动分行输入并模拟回车

    参数：
    - text: 要输入的文本内容，可包含换行符

    行为：
    - 在所有已连接设备上并发执行输入操作
    - 若当前无输入焦点，则输入可能失败或无效

    返回：
    - 各设备输入操作结果列表

    示例：
    send_keys(text="username")
    send_keys(text="user\\npassword")

    Agent 使用语义：
    当输入框已处于焦点状态时使用。
    """

    device_list = await mng.refresh()

    logger.info(f"Send keys {text}")
    return await asyncio.gather(
        *(device.send_keys(text) for device in device_list)
    )


@mcp.tool()
async def tap(x: int, y: int) -> typing.Any:
    """
    在指定屏幕绝对坐标执行点击操作。

    参数：
    - x: 屏幕横坐标
    - y: 屏幕纵坐标

    行为：
    - 在所有已连接设备上并发执行点击

    返回：
    - 各设备点击操作结果列表

    示例：
    tap(x=540, y=1680)

    Agent 使用语义：
    当无法通过控件属性定位时使用坐标点击。
    """

    device_list = await mng.refresh()

    logger.info(f"Tap {x} {y}")
    return await asyncio.gather(
        *(device.tap(x, y) for device in device_list)
    )


@mcp.tool()
async def swipe(x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> typing.Any:
    """
    从起点坐标滑动到终点坐标。

    参数：
    - x1, y1: 起点坐标
    - x2, y2: 终点坐标
    - duration: 滑动耗时（毫秒）

    行为：
    - 在所有已连接设备上并发执行滑动操作

    返回：
    - 各设备滑动操作结果列表

    示例：
    swipe(500, 1500, 500, 500)
    swipe(100, 800, 900, 800, duration=500)

    Agent 使用语义：
    用于页面滚动、列表翻页、拖动操作。
    """

    device_list = await mng.refresh()

    logger.info(f"Swipe {x1} {y1} {x2} {y2} {duration}")
    return await asyncio.gather(
        *(device.swipe(x1, y1, x2, y2, duration) for device in device_list)
    )


@mcp.tool()
async def key_event(keycode: int) -> typing.Any:
    """
    向设备发送 Android 系统按键事件。

    参数：
    - keycode: Android KeyEvent 数值

    常用 KeyCode：
    - 4  → BACK
    - 3  → HOME
    - 66 → ENTER
    - 82 → MENU

    行为：
    - 在所有已连接设备上并发发送按键事件

    返回：
    - 各设备按键事件执行结果列表

    示例：
    key_event(4)
    key_event(66)

    Agent 使用语义：
    用于系统级导航与确认操作。
    """

    device_list = await mng.refresh()

    logger.info(f"KeyEvent {keycode}")
    return await asyncio.gather(
        *(device.key_event(keycode) for device in device_list)
    )


@mcp.tool()
async def sleep(delay: float) -> None:
    """
    等待指定秒数。

    参数：
    - delay: 等待时间（秒，可为小数）

    行为：
    - 当前任务仅按时间延迟，不检测页面状态、不判断加载完成

    返回：
    - 无返回值

    示例：
    sleep(1.5)

    Agent 使用语义：
    仅用于插入固定时间延迟，不能保证页面已加载完成。
    不应作为页面状态判断手段使用。

    约束：
    - sleep 只是时间等待，不代表页面就绪
    - 后续操作仍可能失败，需要结合点击结果或页面验证
    """

    logger.info(f"Wait {delay}")
    await asyncio.sleep(delay)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
