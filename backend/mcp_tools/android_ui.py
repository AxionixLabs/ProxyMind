#     _              _           _     _   _   _ ___
#    / \   _ __   __| |_ __ ___ (_) __| | | | | |_ _|
#   / _ \ | '_ \ / _` | '__/ _ \| |/ _` | | | | || |
#  / ___ \| | | | (_| | | | (_) | | (_| | | |_| || |
# /_/   \_\_| |_|\__,_|_|  \___/|_|\__,_|  \___/|___|
#

import typing
import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.mcp_core.middleware import exception_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @exception_middleware("swipe_unlock")
    async def swipe_unlock() -> None:
        """
        点亮屏幕并通过上滑手势尝试解锁设备。

        功能说明：
        - 用于在设备处于锁屏状态时执行解锁操作
        - 自动检测屏幕是否点亮，若未点亮则先唤醒屏幕
        - 通过从屏幕底部向上滑动的方式触发系统解锁手势
        - 不处理密码、图案或指纹等二次验证，仅负责滑动解锁动作

        参数：
        - 无

        行为：
        - 在所有已连接设备上并发执行解锁操作
        - 若设备已解锁，则该操作可能无实际效果
        - 若设备存在密码或指纹验证，解锁可能失败

        返回：
        - 无返回值

        示例：
        swipe_unlock()

        Agent 使用语义：
        当设备可能处于锁屏或熄屏状态时，
        在执行任何 UI 操作前应优先调用该方法确保设备可交互。
        """

        device_list = await manage.refresh()

        logger.info("Swipe unlock")
        await asyncio.gather(
            *(device.swipe_unlock() for device in device_list)
        )

    @mcp.tool()
    @exception_middleware("swipe")
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

        device_list = await manage.refresh()

        logger.info(f"Swipe {x1} {y1} {x2} {y2} {duration}")
        return await asyncio.gather(
            *(device.swipe(x1, y1, x2, y2, duration) for device in device_list)
        )

    @mcp.tool()
    @exception_middleware("tap")
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

        device_list = await manage.refresh()

        logger.info(f"Tap {x} {y}")
        return await asyncio.gather(
            *(device.tap(x, y) for device in device_list)
        )

    @mcp.tool()
    @exception_middleware("key_event")
    async def key_event(keycode: int) -> typing.Any:
        """
        向设备发送 Android 系统按键事件。

        参数：
        - keycode: Android KeyEvent 数值

        常用 KeyCode 对照：

        导航类：
        - 3   → HOME（返回桌面）
        - 4   → BACK（返回上一页）
        - 82  → MENU（打开菜单）
        - 187 → RECENTS（最近任务）

        输入与确认：
        - 66  → ENTER（回车/确认）
        - 61  → TAB（切换输入框）
        - 67  → DEL（删除/退格）

        系统控制：
        - 24  → VOLUME_UP（音量加）
        - 25  → VOLUME_DOWN（音量减）
        - 26  → POWER（电源/锁屏）
        - 164 → MUTE（静音）

        多媒体：
        - 85  → MEDIA_PLAY_PAUSE
        - 87  → MEDIA_NEXT
        - 88  → MEDIA_PREVIOUS

        行为：
        - 在所有已连接设备上并发发送按键事件
        - 该操作不依赖当前焦点控件
        - 属于系统级输入注入

        返回：
        - 各设备按键事件执行结果列表

        示例：

        # 返回上一页
        key_event(4)

        # 回到桌面
        key_event(3)

        # 确认 / 提交表单
        key_event(66)

        # 打开最近任务
        key_event(187)

        # 删除输入框字符
        key_event(67)

        # 锁屏 / 点亮屏幕
        key_event(26)

        # 音量减
        key_event(25)

        Agent 使用语义：
        用于系统级导航、确认、回退、输入控制及设备状态操作。
        不依赖页面结构，可在任何界面直接使用。
        """

        device_list = await manage.refresh()

        logger.info(f"KeyEvent {keycode}")
        return await asyncio.gather(
            *(device.key_event(keycode) for device in device_list)
        )

    @mcp.tool()
    @exception_middleware("click")
    async def click(by: typing.Literal["text", "id", "desc"], value: str) -> typing.Any:
        """
        按指定 UI 属性精确匹配并点击第一个命中的控件。

        参数：
        - by:
            - "text" → 按控件 text 属性精确匹配
            - "id"   → 按控件 resource-id 精确匹配
            - "desc" → 按控件 content-desc 精确匹配
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
        click(by="id", value="com.xx:id/login_btn")
        click(by="desc", value="login_button")

        Agent 使用语义：
        当需要对明确 UI 元素执行点击操作时使用。

        约束：
        - 仅支持精确匹配
        - 不支持 contains / 正则 / 模糊匹配
        - UI 文案或 ID 变化将导致匹配失败
        """

        device_list = await manage.refresh()

        logger.info(f"Click by {by} value={value}")
        return await asyncio.gather(
            *(device.click(by, value) for device in device_list)
        )

    @mcp.tool()
    @exception_middleware("send_keys")
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

        device_list = await manage.refresh()

        logger.info(f"Send keys {text}")
        return await asyncio.gather(
            *(device.send_keys(text) for device in device_list)
        )

    @mcp.tool()
    @exception_middleware("combo_key")
    async def combo_key(first: int, *others: int) -> typing.Any:
        """
        执行组合按键操作（模拟多个按键几乎同时触发）。

        功能说明：
        - 用于模拟系统级组合按键行为（如截图、电源组合键等）
        - 第一个按键以长按方式触发，并在后台执行
        - 后续按键按顺序依次触发
        - 按键执行发生在设备端 shell 中，保证组合语义正确

        参数：
        - first: 组合按键中的第一个按键码（将以长按方式触发）
        - others: 后续需要组合触发的按键码列表

        行为：
        - 在所有已连接设备上并发执行组合按键操作
        - 第一个按键使用 --longpress 并后台执行
        - 后续按键按顺序触发，默认间隔约 0.03 秒
        - 该方式适用于截图、系统快捷键等需要近同时按下的场景

        返回：
        - 各设备组合按键执行结果列表

        示例：
        combo_key(25, 26)        # 音量减 + 电源（截图）
        combo_key(24, 26)        # 音量加 + 电源
        combo_key(3, 4)          # HOME + BACK（示例）

        Agent 使用语义：
        当需要模拟系统组合按键（如截图、系统快捷操作）时使用。
        不用于普通点击或文本输入场景。
        """

        device_list = await manage.refresh()

        logger.info(f"Combo key first={first} others={others}")
        return await asyncio.gather(
            *(device.combo_key(first, *others) for device in device_list)
        )


if __name__ == '__main__':
    pass
