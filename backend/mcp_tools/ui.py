#  _   _ ___
# | | | |_ _|
# | | | || |
# | |_| || |
#  \___/|___|
#

import typing
import asyncio
from loguru import logger
from mcp.server import FastMCP
from backend.middlewares.mid_task import task_middleware
from engine.manage import DeviceManage


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("lock_screen")
    async def lock_screen() -> None:
        """
        锁定设备屏幕（熄屏进入锁屏状态）。

        功能说明：
        - 用于将设备切换到锁屏/熄屏状态
        - 仅在屏幕处于点亮状态时触发锁屏，避免误唤醒
        - 通过系统电源键语义完成锁屏（Android keycode=26）

        参数：
        - 无

        行为：
        - 在所有已连接设备上并发执行锁屏操作
        - 若设备已处于熄屏/锁屏状态，则该操作不产生变化

        返回：
        - 无返回值

        示例：
        lock_screen()

        Agent 使用语义：
        当需要结束交互、进入待机、或在测试中重置设备状态时使用。
        建议与 swipe_unlock 成对使用，形成“可交互态 ↔ 锁屏态”的闭环控制。
        """
        device_list = await manage.refresh()

        logger.info("Lock screen")
        await asyncio.gather(
            *(device.lock_screen() for device in device_list)
        )

    @mcp.tool()
    @task_middleware("press_power")
    async def press_power(longpress: bool = False) -> typing.Any:
        """
        电源键（POWER 键）。

        功能说明：
        - 通过系统电源键语义发送按键事件（Android keycode=26）
        - 可用于锁屏或唤起系统级电源菜单

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 在所有已连接设备上并发发送电源键事件
        - 不依赖当前应用或页面状态
        - longpress=false：发送普通 POWER 按键事件
        - longpress=true：发送 POWER 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备电源键操作结果列表

        示例：
        press_power()
        press_power(longpress=True)

        Agent 使用语义：
        当需要锁屏、唤起电源菜单或系统级快捷能力时使用。
        该操作属于系统级按键控制，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按电源键/按住电源键”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent POWER")
        return await asyncio.gather(
            *(device.key_event(26, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("swipe_unlock")
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
    @task_middleware("swipe")
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
        swipe(100, 800, 900, 800, duration=300)

        Agent 使用语义：
        用于页面滚动、列表翻页、拖动操作。
        """
        device_list = await manage.refresh()

        logger.info(f"Swipe {x1} {y1} {x2} {y2} {duration}")
        return await asyncio.gather(
            *(device.swipe(x1, y1, x2, y2, duration) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("tap")
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
    @task_middleware("click")
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

        Agent 使用语义：
        当需要对明确 UI 元素执行点击操作时使用。
        """
        device_list = await manage.refresh()

        logger.info(f"Click by {by} value={value}")
        return await asyncio.gather(
            *(device.click(by, value) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("send_keys")
    async def send_keys(text: str) -> typing.Any:
        """
        向当前已聚焦的输入框逐行输入文本。

        功能说明：
        - 仅向当前获得输入焦点的控件输入文本
        - 不进行任何控件定位或点击操作

        参数：
        - text: 要输入的文本内容，可包含换行符

        行为：
        - 在所有已连接设备上并发执行输入操作
        - 若当前无输入焦点，则输入可能失败或无效

        返回：
        - 各设备输入操作结果列表

        示例：
        send_keys(text="username")

        Agent 使用语义：
        当输入框已处于焦点状态时使用。
        """
        device_list = await manage.refresh()

        logger.info(f"Send keys {text}")
        return await asyncio.gather(
            *(device.send_keys(text) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("combo_key")
    async def combo_key(first: int, others: list[int]) -> typing.Any:
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
        combo_key(25, 26)  # 音量减 + 电源（截图）

        Agent 使用语义：
        当需要模拟系统组合按键（如截图、系统快捷操作）时使用。
        不用于普通点击或文本输入场景。
        """
        device_list = await manage.refresh()

        logger.info(f"Combo key first={first} others={others}")
        return await asyncio.gather(
            *(device.combo_key(first, *others) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("deep_link")
    async def deep_link(url: str) -> typing.Any:
        """
        通过深度链接启动指定的应用服务或页面。

        参数：
        - url: 深度链接 URL（如 scheme://path）

        行为：
        - 通过 adb shell am start -a VIEW -d 启动
        - 在所有已连接设备上并发执行

        返回：
        - 各设备深链启动结果列表

        示例：
        deep_link("application://camera/live")

        Agent 使用语义：
        当需要通过协议链接直接跳转应用内部页面时使用。
        """
        device_list = await manage.refresh()

        logger.info(f"Deep link {url}")
        return await asyncio.gather(
            *(device.deep_link(url) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("app_start")
    async def app_start(package: str) -> typing.Any:
        """
        启动指定包名的应用。

        参数：
        - package: 应用包名（如 com.xx.app）

        行为：
        - 通过 adb monkey 启动应用主入口
        - 在所有已连接设备上并发执行

        返回：
        - 各设备启动结果列表

        示例：
        app_start("com.android.settings")

        Agent 使用语义：
        当用户语义为“打开/启动某应用”时优先使用该方法。
        """
        device_list = await manage.refresh()

        logger.info(f"App start {package}")
        return await asyncio.gather(
            *(device.app_start(package) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("force_stop")
    async def force_stop(package: str) -> typing.Any:
        """
        强制停止指定包名的应用。

        参数：
        - package: 应用包名

        行为：
        - 通过 adb shell am force-stop 终止应用进程
        - 在所有已连接设备上并发执行

        返回：
        - 各设备停止结果列表

        示例：
        force_stop("com.android.settings")

        Agent 使用语义：
        当需要重启应用、清理状态或关闭后台应用时使用。
        """
        device_list = await manage.refresh()

        logger.info(f"Force stop {package}")
        return await asyncio.gather(
            *(device.force_stop(package) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("open_notification")
    async def open_notification() -> typing.Any:
        """
        打开通知栏（Notification Panel）。

        参数：
        - 无

        行为：
        - 向设备发送系统通知栏展开指令
        - 从屏幕顶部下拉打开系统通知面板
        - 不依赖当前应用界面或输入焦点

        返回：
        - 各设备打开通知栏操作执行结果列表

        示例：
        open_notification()

        Agent 使用语义：
        当需要查看系统通知、消息提醒或状态栏信息时使用。
        该操作属于系统级界面控制，不可使用 click 或 tap 模拟。
        """

        device_list = await manage.refresh()

        logger.info("Open notification panel")
        return await asyncio.gather(
            *(device.open_notification() for device in device_list)
        )

    @mcp.tool()
    @task_middleware("open_quick_settings")
    async def open_quick_settings() -> typing.Any:
        """
        打开快速设置面板（Quick Settings Panel）。

        参数：
        - 无

        行为：
        - 向设备发送系统快速设置展开指令
        - 打开包含 WiFi、蓝牙、飞行模式等系统开关的快捷面板
        - 不依赖当前应用界面或输入焦点

        返回：
        - 各设备打开快速设置面板操作执行结果列表

        示例：
        open_quick_settings()

        Agent 使用语义：
        当需要快速切换系统功能开关或查看系统状态时使用。
        该操作属于系统级界面控制，不可使用 click 或 tap 模拟。
        """

        device_list = await manage.refresh()

        logger.info("Open quick settings panel")
        return await asyncio.gather(
            *(device.open_quick_settings() for device in device_list)
        )

    @mcp.tool()
    @task_middleware("go_home")
    async def go_home(longpress: bool = False) -> typing.Any:
        """
        返回桌面（HOME 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统 HOME 按键事件
        - 立即切换到桌面界面
        - 不依赖当前应用或页面状态
        - longpress=false：发送普通 HOME 按键事件
        - longpress=true：发送 HOME 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备返回桌面操作结果列表

        示例：
        go_home()
        go_home(longpress=True)

        Agent 使用语义：
        当需要结束当前页面流程、回到桌面或重新开始导航路径时使用。
        该操作属于系统级导航，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按主页/按住HOME”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent HOME")
        return await asyncio.gather(
            *(device.key_event(3, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("go_back")
    async def go_back(longpress: bool = False) -> typing.Any:
        """
        返回上一页（BACK 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统 BACK 按键事件
        - 回退到上一级页面或关闭当前弹窗
        - longpress=false：发送普通 BACK 按键事件
        - longpress=true：发送 BACK 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备返回上一页操作结果列表

        示例：
        go_back()
        go_back(longpress=True)

        Agent 使用语义：
        当需要退出当前页面、关闭弹窗、返回上一级界面时使用。
        该操作属于系统级导航，不依赖页面结构，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按返回/按住BACK”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent BACK")
        return await asyncio.gather(
            *(device.key_event(4, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("open_recents")
    async def open_recents(longpress: bool = False) -> typing.Any:
        """
        打开最近任务列表（RECENTS 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统 RECENTS 按键事件
        - 打开系统最近任务切换界面
        - 不依赖当前应用或页面状态
        - longpress=false：发送普通 RECENTS 按键事件
        - longpress=true：发送 RECENTS 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备打开最近任务列表操作结果列表

        示例：
        open_recents()
        open_recents(longpress=True)

        Agent 使用语义：
        当需要切换应用、查看后台任务或恢复最近应用时使用。
        该操作属于系统级导航，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按最近任务/按住RECENTS”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent RECENTS")
        return await asyncio.gather(
            *(device.key_event(187, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("open_menu")
    async def open_menu(longpress: bool = False) -> typing.Any:
        """
        打开系统菜单（MENU 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统 MENU 按键事件
        - 打开当前页面对应的系统或应用菜单
        - longpress=false：发送普通 MENU 按键事件
        - longpress=true：发送 MENU 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备打开菜单操作结果列表

        示例：
        open_menu()
        open_menu(longpress=True)

        Agent 使用语义：
        当需要唤出页面功能菜单或系统菜单选项时使用。
        该操作属于系统级导航，不依赖页面结构，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按菜单/按住MENU”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent MENU")
        return await asyncio.gather(
            *(device.key_event(82, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("press_enter")
    async def press_enter(longpress: bool = False) -> typing.Any:
        """
        确认 / 回车（ENTER 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统 ENTER 按键事件
        - 触发表单提交、确认操作或换行确认行为
        - 不依赖页面结构，仅依赖当前输入焦点
        - longpress=false：发送普通 ENTER 按键事件
        - longpress=true：发送 ENTER 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备确认操作执行结果列表

        示例：
        press_enter()
        press_enter(longpress=True)

        Agent 使用语义：
        当需要确认输入、提交表单、执行默认确认操作时使用。
        该操作属于系统级输入控制，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按回车/按住ENTER”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent ENTER")
        return await asyncio.gather(
            *(device.key_event(66, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("press_tab")
    async def press_tab(longpress: bool = False) -> typing.Any:
        """
        切换输入焦点（TAB 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统 TAB 按键事件
        - 将输入焦点切换到下一个可输入控件
        - 适用于表单类页面的输入流程控制
        - longpress=false：发送普通 TAB 按键事件
        - longpress=true：发送 TAB 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备焦点切换操作执行结果列表

        示例：
        press_tab()
        press_tab(longpress=True)

        Agent 使用语义：
        当存在多个输入框需要顺序填写时使用。
        该操作属于系统级输入导航，不依赖页面结构。
        只有用户明确表达“长按TAB/按住TAB”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent TAB")
        return await asyncio.gather(
            *(device.key_event(61, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("press_delete")
    async def press_delete(longpress: bool = False) -> typing.Any:
        """
        删除字符（DEL / BACKSPACE 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统 DELETE 按键事件
        - 删除当前输入框光标前的字符
        - 若无输入焦点，则该操作可能无效果
        - longpress=false：发送普通 DELETE 按键事件
        - longpress=true：发送 DELETE 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备删除操作执行结果列表

        示例：
        press_delete()
        press_delete(longpress=True)

        Agent 使用语义：
        当需要修正输入内容、清空字符或逐字删除输入时使用。
        该操作属于系统级输入控制，不依赖控件定位。
        只有用户明确表达“长按删除/按住DEL”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent DELETE")
        return await asyncio.gather(
            *(device.key_event(67, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("press_space")
    async def press_space(longpress: bool = False) -> typing.Any:
        """
        输入空格字符（SPACE 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统空格按键事件
        - 在当前输入焦点位置插入一个空格字符
        - 若无输入焦点，则该操作可能无效果
        - longpress=false：发送普通 SPACE 按键事件
        - longpress=true：发送 SPACE 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备空格输入操作执行结果列表

        示例：
        press_space()
        press_space(longpress=True)

        Agent 使用语义：
        当需要在输入内容中插入空格或分隔文本时使用。
        该操作属于系统级输入控制，不依赖控件定位。
        只有用户明确表达“长按空格/按住SPACE”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent SPACE")
        return await asyncio.gather(
            *(device.key_event(62, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("press_escape")
    async def press_escape(longpress: bool = False) -> typing.Any:
        """
        取消当前操作或退出输入状态（ESC 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统 ESC 按键事件
        - 取消当前输入、关闭输入法候选框或退出编辑状态
        - 在部分应用中可作为返回或取消操作使用
        - longpress=false：发送普通 ESC 按键事件
        - longpress=true：发送 ESC 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备 ESC 操作执行结果列表

        示例：
        press_escape()
        press_escape(longpress=True)

        Agent 使用语义：
        当需要取消当前输入、退出编辑态或关闭输入框时使用。
        该操作属于系统级输入控制，不依赖页面结构。
        只有用户明确表达“长按退出/按住ESC”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent ESCAPE")
        return await asyncio.gather(
            *(device.key_event(111, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("press_voice_assist")
    async def press_voice_assist(longpress: bool = False) -> typing.Any:
        """
        打开系统语音助手（VOICE ASSIST 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统语音助手按键事件
        - 唤起系统默认语音助理（如 Google Assistant）
        - 不依赖当前应用界面或输入焦点
        - longpress=false：发送普通 VOICE_ASSIST 按键事件
        - longpress=true：发送 VOICE_ASSIST 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备语音助手唤起操作执行结果列表

        示例：
        press_voice_assist()
        press_voice_assist(longpress=True)

        Agent 使用语义：
        当需要唤起系统语音助手、语音搜索或语音控制入口时使用。
        该操作属于系统级快捷入口，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按语音助手/按住VOICE_ASSIST”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent VOICE_ASSIST")
        return await asyncio.gather(
            *(device.key_event(231, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("volume_up")
    async def volume_up(longpress: bool = False) -> typing.Any:
        """
        增加系统音量（VOLUME_UP 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统音量增加按键事件
        - 提高当前系统媒体或铃声音量级别
        - 不依赖当前应用界面或输入焦点
        - longpress=false：发送普通 VOLUME_UP 按键事件
        - longpress=true：发送 VOLUME_UP 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备音量增加操作执行结果列表

        示例：
        volume_up()
        volume_up(longpress=True)

        Agent 使用语义：
        当需要提升音量、调整播放声音大小时使用。
        该操作属于系统级音量控制，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按音量加/按住VOLUME_UP”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent VOLUME_UP")
        return await asyncio.gather(
            *(device.key_event(24, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("volume_down")
    async def volume_down(longpress: bool = False) -> typing.Any:
        """
        降低系统音量（VOLUME_DOWN 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统音量降低按键事件
        - 降低当前系统媒体或铃声音量级别
        - 不依赖当前应用界面或输入焦点
        - longpress=false：发送普通 VOLUME_DOWN 按键事件
        - longpress=true：发送 VOLUME_DOWN 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备音量降低操作执行结果列表

        示例：
        volume_down()
        volume_down(longpress=True)

        Agent 使用语义：
        当需要降低音量、静音前调节音量时使用。
        该操作属于系统级音量控制，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按音量减/按住VOLUME_DOWN”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent VOLUME_DOWN")
        return await asyncio.gather(
            *(device.key_event(25, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("volume_mute")
    async def volume_mute(longpress: bool = False) -> typing.Any:
        """
        静音系统音量（MUTE 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统静音按键事件
        - 立即将当前系统音量切换为静音状态
        - 不依赖当前应用界面或输入焦点
        - longpress=false：发送普通 MUTE 按键事件
        - longpress=true：发送 MUTE 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备静音操作执行结果列表

        示例：
        volume_mute()
        volume_mute(longpress=True)

        Agent 使用语义：
        当需要快速关闭声音输出或进入静音环境时使用。
        该操作属于系统级音量控制，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按静音/按住MUTE”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent MUTE")
        return await asyncio.gather(
            *(device.key_event(164, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("media_play_pause")
    async def media_play_pause(longpress: bool = False) -> typing.Any:
        """
        播放 / 暂停多媒体（MEDIA_PLAY_PAUSE 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统播放/暂停按键事件
        - 控制当前前台或后台的多媒体播放状态
        - 不依赖页面结构或当前应用界面
        - longpress=false：发送普通 MEDIA_PLAY_PAUSE 按键事件
        - longpress=true：发送 MEDIA_PLAY_PAUSE 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备播放/暂停操作执行结果列表

        示例：
        media_play_pause()
        media_play_pause(longpress=True)

        Agent 使用语义：
        当需要控制音乐、视频或系统多媒体播放状态时使用。
        该操作属于系统级多媒体控制，不可使用 click 或 tap 模拟。
        只有用户明确表达“长按播放暂停/按住MEDIA_PLAY_PAUSE”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent MEDIA_PLAY_PAUSE")
        return await asyncio.gather(
            *(device.key_event(85, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("media_next")
    async def media_next(longpress: bool = False) -> typing.Any:
        """
        切换到下一首媒体（MEDIA_NEXT 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统下一首按键事件
        - 控制当前多媒体播放器切换到下一条媒体内容
        - 不依赖当前应用界面
        - longpress=false：发送普通 MEDIA_NEXT 按键事件
        - longpress=true：发送 MEDIA_NEXT 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备下一首操作执行结果列表

        示例：
        media_next()
        media_next(longpress=True)

        Agent 使用语义：
        当需要跳过当前播放内容并进入下一首媒体时使用。
        该操作属于系统级多媒体控制，不依赖页面结构。
        只有用户明确表达“长按下一首/按住MEDIA_NEXT”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent MEDIA_NEXT")
        return await asyncio.gather(
            *(device.key_event(87, longpress) for device in device_list)
        )

    @mcp.tool()
    @task_middleware("media_previous")
    async def media_previous(longpress: bool = False) -> typing.Any:
        """
        切换到上一首媒体（MEDIA_PREVIOUS 键）。

        参数：
        - longpress: 是否长按该按键（默认 false）

        行为：
        - 向设备发送系统上一首按键事件
        - 控制当前多媒体播放器切换到上一条媒体内容
        - 不依赖当前应用界面
        - longpress=false：发送普通 MEDIA_PREVIOUS 按键事件
        - longpress=true：发送 MEDIA_PREVIOUS 长按事件（部分系统用于触发系统级快捷能力）

        返回：
        - 各设备上一首操作执行结果列表

        示例：
        media_previous()
        media_previous(longpress=True)

        Agent 使用语义：
        当需要返回上一条媒体内容重新播放时使用。
        该操作属于系统级多媒体控制，不依赖页面结构。
        只有用户明确表达“长按上一首/按住MEDIA_PREVIOUS”时才使用 longpress=true
        """
        device_list = await manage.refresh()

        logger.info("KeyEvent MEDIA_PREVIOUS")
        return await asyncio.gather(
            *(device.key_event(88, longpress) for device in device_list)
        )


if __name__ == '__main__':
    pass
