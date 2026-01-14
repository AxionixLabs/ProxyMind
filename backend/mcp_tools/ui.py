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
        """Class: system; Action: 锁屏/熄屏(POWER=26); Args: none; Use: 结束交互/重置状态; Return: None; Notes: screen-on 才执行, 已锁屏 no-op."""
        device_list = await manage.refresh()

        logger.info("Lock screen")
        await asyncio.gather(
            *(device.lock_screen() for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("press_power")
    async def press_power(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 电源键(keycode=26); Args: longpress(bool)=False; Use: 锁屏/电源菜单; Return: list[device_result]; Notes: longpress 仅在用户明确“长按电源”时使用."""
        device_list = await manage.refresh()

        logger.info("KeyEvent POWER")
        return await asyncio.gather(
            *(device.key_event(26, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("swipe_unlock")
    async def swipe_unlock() -> None:
        """Class: system; Action: 点亮并上滑解锁; Args: none; Use: UI 操作前确保可交互; Return: None; Notes: 不处理密码/指纹等二次验证."""
        device_list = await manage.refresh()

        logger.info("Swipe unlock")
        await asyncio.gather(
            *(device.swipe_unlock() for device in device_list), return_exceptions=True
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
    @task_middleware("send_keys")
    async def send_keys(text: str) -> typing.Any:
        """Class: ui; Action: 输入文本到当前焦点; Args: text(str); Use: 输入框已聚焦时输入; Return: list[device_result]; Notes: 不负责定位/点击, 无焦点可能失败."""
        device_list = await manage.refresh()

        logger.info(f"Send keys {text}")
        return await asyncio.gather(
            *(device.send_keys(text) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("combo_key")
    async def combo_key(first: int, others: list[int]) -> typing.Any:
        """Class: system; Action: 组合按键(first长按+others); Args: first(int keycode), others(list[int]); Use: 截图/系统快捷键; Return: list[device_result]; Notes: shell-level, 近同时触发."""
        device_list = await manage.refresh()

        logger.info(f"Combo key first={first} others={others}")
        return await asyncio.gather(
            *(device.combo_key(first, *others) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("deep_link")
    async def deep_link(url: str) -> typing.Any:
        """Class: app; Action: 深链跳转(am start VIEW); Args: url(str); Use: 直达应用内部页面/服务; Return: list[device_result]; Notes: 需系统存在 handler."""
        device_list = await manage.refresh()

        logger.info(f"Deep link {url}")
        return await asyncio.gather(
            *(device.deep_link(url) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("app_start")
    async def app_start(package: str) -> typing.Any:
        """Class: app; Action: 启动应用(monkey); Args: package(str); Use: 用户语义“打开/启动某应用”优先; Return: list[device_result]; Notes: 启动主入口, 非指定 Activity."""
        device_list = await manage.refresh()

        logger.info(f"App start {package}")
        return await asyncio.gather(
            *(device.app_start(package) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("force_stop")
    async def force_stop(package: str) -> typing.Any:
        """Class: app; Action: 强制停止应用(force-stop); Args: package(str); Use: 重启应用/清理状态; Return: list[device_result]; Notes: 终止进程与后台任务."""
        device_list = await manage.refresh()

        logger.info(f"Force stop {package}")
        return await asyncio.gather(
            *(device.force_stop(package) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("open_notification")
    async def open_notification() -> typing.Any:
        """Class: system; Action: 打开通知栏; Args: none; Use: 查看系统通知/状态; Return: list[device_result]; Notes: 系统级 UI, 禁止用 click/tap 模拟."""
        device_list = await manage.refresh()

        logger.info("Open notification panel")
        return await asyncio.gather(
            *(device.open_notification() for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("open_quick_settings")
    async def open_quick_settings() -> typing.Any:
        """Class: system; Action: 打开快速设置; Args: none; Use: WiFi/蓝牙等开关面板; Return: list[device_result]; Notes: 系统级 UI, 禁止用 click/tap 模拟."""
        device_list = await manage.refresh()

        logger.info("Open quick settings panel")
        return await asyncio.gather(
            *(device.open_quick_settings() for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("go_home")
    async def go_home(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 回到桌面(keycode=3); Args: longpress(bool)=False; Use: 系统导航回桌面; Return: list[device_result]; Notes: “长按主页”才用 longpress=True."""
        device_list = await manage.refresh()

        logger.info("KeyEvent HOME")
        return await asyncio.gather(
            *(device.key_event(3, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("go_back")
    async def go_back(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 返回上一页(keycode=4); Args: longpress(bool)=False; Use: 回退/关闭弹窗; Return: list[device_result]; Notes: “长按返回”才用 longpress=True."""
        device_list = await manage.refresh()

        logger.info("KeyEvent BACK")
        return await asyncio.gather(
            *(device.key_event(4, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("open_recents")
    async def open_recents(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 打开最近任务(keycode=187); Args: longpress(bool)=False; Use: 切换应用/后台任务; Return: list[device_result]; Notes: Recents 属于系统导航, 禁止用 click."""
        device_list = await manage.refresh()

        logger.info("KeyEvent RECENTS")
        return await asyncio.gather(
            *(device.key_event(187, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("open_menu")
    async def open_menu(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 打开菜单(keycode=82); Args: longpress(bool)=False; Use: 系统/应用菜单; Return: list[device_result]; Notes: 系统级入口, 禁止用 click/tap 模拟."""
        device_list = await manage.refresh()

        logger.info("KeyEvent MENU")
        return await asyncio.gather(
            *(device.key_event(82, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("press_enter")
    async def press_enter(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 回车确认(keycode=66); Args: longpress(bool)=False; Use: 提交/确认/默认操作; Return: list[device_result]; Notes: 依赖输入焦点."""
        device_list = await manage.refresh()

        logger.info("KeyEvent ENTER")
        return await asyncio.gather(
            *(device.key_event(66, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("press_tab")
    async def press_tab(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 焦点切换(keycode=61); Args: longpress(bool)=False; Use: 表单输入流切换输入框; Return: list[device_result]; Notes: 依赖页面可聚焦控件."""
        device_list = await manage.refresh()

        logger.info("KeyEvent TAB")
        return await asyncio.gather(
            *(device.key_event(61, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("press_delete")
    async def press_delete(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 删除字符(keycode=67); Args: longpress(bool)=False; Use: 删除/修正输入; Return: list[device_result]; Notes: 依赖输入焦点."""
        device_list = await manage.refresh()

        logger.info("KeyEvent DELETE")
        return await asyncio.gather(
            *(device.key_event(67, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("press_space")
    async def press_space(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 输入空格(keycode=62); Args: longpress(bool)=False; Use: 插入空格/分隔文本; Return: list[device_result]; Notes: 依赖输入焦点."""
        device_list = await manage.refresh()

        logger.info("KeyEvent SPACE")
        return await asyncio.gather(
            *(device.key_event(62, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("press_escape")
    async def press_escape(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 取消/退出编辑(keycode=111); Args: longpress(bool)=False; Use: 取消输入/退出编辑态; Return: list[device_result]; Notes: 部分应用等价“取消/返回”."""
        device_list = await manage.refresh()

        logger.info("KeyEvent ESCAPE")
        return await asyncio.gather(
            *(device.key_event(111, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("press_voice_assist")
    async def press_voice_assist(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 语音助手(keycode=231); Args: longpress(bool)=False; Use: 唤起系统语音助理; Return: list[device_result]; Notes: 系统级快捷入口, 禁止 click/tap."""
        device_list = await manage.refresh()

        logger.info("KeyEvent VOICE_ASSIST")
        return await asyncio.gather(
            *(device.key_event(231, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("volume_up")
    async def volume_up(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 音量加(keycode=24); Args: longpress(bool)=False; Use: 调高音量; Return: list[device_result]; Notes: 系统级按键."""
        device_list = await manage.refresh()

        logger.info("KeyEvent VOLUME_UP")
        return await asyncio.gather(
            *(device.key_event(24, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("volume_down")
    async def volume_down(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 音量减(keycode=25); Args: longpress(bool)=False; Use: 调低音量; Return: list[device_result]; Notes: 系统级按键."""
        device_list = await manage.refresh()

        logger.info("KeyEvent VOLUME_DOWN")
        return await asyncio.gather(
            *(device.key_event(25, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("volume_mute")
    async def volume_mute(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 静音(keycode=164); Args: longpress(bool)=False; Use: 快速静音; Return: list[device_result]; Notes: 系统级按键."""
        device_list = await manage.refresh()

        logger.info("KeyEvent MUTE")
        return await asyncio.gather(
            *(device.key_event(164, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("media_play_pause")
    async def media_play_pause(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 播放/暂停(keycode=85); Args: longpress(bool)=False; Use: 控制系统媒体播放; Return: list[device_result]; Notes: 不依赖当前应用 UI."""
        device_list = await manage.refresh()

        logger.info("KeyEvent MEDIA_PLAY_PAUSE")
        return await asyncio.gather(
            *(device.key_event(85, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("media_next")
    async def media_next(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 下一首(keycode=87); Args: longpress(bool)=False; Use: 切歌/切换媒体; Return: list[device_result]; Notes: 系统媒体控制."""
        device_list = await manage.refresh()

        logger.info("KeyEvent MEDIA_NEXT")
        return await asyncio.gather(
            *(device.key_event(87, longpress) for device in device_list), return_exceptions=True
        )

    @mcp.tool()
    @task_middleware("media_previous")
    async def media_previous(longpress: bool = False) -> typing.Any:
        """Class: system; Action: 上一首(keycode=88); Args: longpress(bool)=False; Use: 返回上一条媒体; Return: list[device_result]; Notes: 系统媒体控制."""
        device_list = await manage.refresh()

        logger.info("KeyEvent MEDIA_PREVIOUS")
        return await asyncio.gather(
            *(device.key_event(88, longpress) for device in device_list), return_exceptions=True
        )


if __name__ == '__main__':
    pass
