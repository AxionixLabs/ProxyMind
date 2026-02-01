#  ____            _                    ____            _             _
# / ___| _   _ ___| |_ ___ _ __ ___    / ___|___  _ __ | |_ _ __ ___ | |
# \___ \| | | / __| __/ _ \ '_ ` _ \  | |   / _ \| '_ \| __| '__/ _ \| |
#  ___) | |_| \__ \ ||  __/ | | | | | | |__| (_) | | | | |_| | | (_) | |
# |____/ \__, |___/\__\___|_| |_| |_|  \____\___/|_| |_|\__|_|  \___/|_|
#        |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

from mcp.server import FastMCP
from mcp.types import CallToolResult
from backend.mcp_hub.hub_manage import DeviceManage
from backend.middlewares.mid_task import task_middleware
from backend.utilities.toolbox import broadcast


def bind(mcp: FastMCP, manage: DeviceManage) -> None:

    @mcp.tool()
    @task_middleware("open_notification")
    async def open_notification() -> CallToolResult:
        """Class: system; Action: 打开通知栏; Args: none; Use: 查看系统通知/状态; Return: CallToolResult(text + structuredContent); Notes: 系统级 UI, 禁止用 click/tap 模拟。"""
        return await broadcast(
            tool="open_notification",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.open_notification()
        )

    @mcp.tool()
    @task_middleware("open_quick_settings")
    async def open_quick_settings() -> CallToolResult:
        """Class: system; Action: 打开快速设置; Args: none; Use: WiFi/蓝牙等开关面板; Return: CallToolResult(text + structuredContent); Notes: 系统级 UI, 禁止用 click/tap 模拟。"""
        return await broadcast(
            tool="open_quick_settings",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.open_quick_settings()
        )

    @mcp.tool()
    @task_middleware("combo_key")
    async def combo_key(first: int, others: list[int]) -> CallToolResult:
        """Class: system; Action: 组合按键(first长按+others); Args: first(int keycode), others(list[int]); Use: 截图/系统快捷键; Return: CallToolResult(text + structuredContent); Notes: shell-level, 近同时触发。"""
        return await broadcast(
            tool="combo_key",
            args={"first": first, "others": others},
            target_list=manage.snapshot,
            call=lambda agent: agent.combo_key(first, *others)
        )

    @mcp.tool()
    @task_middleware("swipe_unlock")
    async def swipe_unlock() -> CallToolResult:
        """Class: system; Action: 点亮并上滑解锁; Args: none; Use: UI 操作前确保可交互; Return: None; Notes: 不处理密码/指纹等二次验证。"""
        return await broadcast(
            tool="swipe_unlock",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.swipe_unlock()
        )

    @mcp.tool()
    @task_middleware("screen_on")
    async def screen_on() -> CallToolResult:
        """Class: system; Action: 点亮屏幕(keycode=26); Args: none; Use: 确保设备可交互; Return: None; Notes: 幂等：已亮则 no-op，仅在熄屏时发送 POWER。"""
        return await broadcast(
            tool="screen_on",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.screen_set(True)
        )

    @mcp.tool()
    @task_middleware("screen_off")
    async def screen_off() -> CallToolResult:
        """Class: system; Action: 锁屏/熄屏(keycode=26); Args: none; Use: 结束交互/重置状态; Return: None; Notes: screen-on 才执行, 已锁屏 no-op."""
        return await broadcast(
            tool="screen_off",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.screen_set(False)
        )

    @mcp.tool()
    @task_middleware("bluetooth_on")
    async def bluetooth_on() -> CallToolResult:
        """Class: system; Action: 打开蓝牙(svc); Args: none; Use: 打开蓝牙；Return: CallToolResult(text + structuredContent); Notes: adb shell svc bluetooth enable."""
        return await broadcast(
            tool="bluetooth_on",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.bluetooth_set("enable")
        )

    @mcp.tool()
    @task_middleware("bluetooth_off")
    async def bluetooth_off() -> CallToolResult:
        """Class: system; Action: 关闭蓝牙(svc); Args: none; Use: 关闭蓝牙；Return: CallToolResult(text + structuredContent); Notes: adb shell svc bluetooth disable."""
        return await broadcast(
            tool="bluetooth_off",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.bluetooth_set("disable")
        )

    @mcp.tool()
    @task_middleware("wifi_on")
    async def wifi_on() -> CallToolResult:
        """Class: system; Action: 打开WiFi(svc); Args: none; Use: 打开WiFi；Return: CallToolResult(text + structuredContent); Notes: adb shell svc wifi enable."""
        return await broadcast(
            tool="wifi_on",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.wifi_set("enable")
        )

    @mcp.tool()
    @task_middleware("wifi_off")
    async def wifi_off() -> CallToolResult:
        """Class: system; Action: 关闭WiFi(svc); Args: none; Use: 关闭WiFi；Return: CallToolResult(text + structuredContent); Notes: adb shell svc wifi disable."""
        return await broadcast(
            tool="wifi_off",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.wifi_set("disable")
        )

    @mcp.tool()
    @task_middleware("data_on")
    async def data_on() -> CallToolResult:
        """Class: system; Action: 打开移动数据(svc); Args: none; Use: 打开移动数据；Return: CallToolResult(text + structuredContent); Notes: adb shell svc data enable."""
        return await broadcast(
            tool="data_on",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.data_set("enable")
        )

    @mcp.tool()
    @task_middleware("data_off")
    async def data_off() -> CallToolResult:
        """Class: system; Action: 关闭移动数据(svc); Args: none; Use: 关闭移动数据；Return: CallToolResult(text + structuredContent); Notes: adb shell svc data disable."""
        return await broadcast(
            tool="data_off",
            args={},
            target_list=manage.snapshot,
            call=lambda agent: agent.data_set("disable")
        )

    @mcp.tool()
    @task_middleware("go_home")
    async def go_home(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 回到桌面(keycode=3); Args: longpress(bool)=False; Use: 系统导航回桌面; Return: CallToolResult(text + structuredContent); Notes: “长按主页”才用 longpress=True."""
        return await broadcast(
            tool="go_home",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(3, longpress)
        )

    @mcp.tool()
    @task_middleware("go_back")
    async def go_back(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 返回上一页(keycode=4); Args: longpress(bool)=False; Use: 回退/关闭弹窗; Return: CallToolResult(text + structuredContent); Notes: “长按返回”才用 longpress=True."""
        return await broadcast(
            tool="go_back",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(4, longpress)
        )

    @mcp.tool()
    @task_middleware("open_recents")
    async def open_recents(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 打开最近任务(keycode=187); Args: longpress(bool)=False; Use: 切换应用/后台任务; Return: CallToolResult(text + structuredContent); Notes: Recents 属于系统导航, 禁止用 click."""
        return await broadcast(
            tool="open_recents",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(187, longpress)
        )

    @mcp.tool()
    @task_middleware("open_menu")
    async def open_menu(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 打开菜单(keycode=82); Args: longpress(bool)=False; Use: 系统/应用菜单; Return: CallToolResult(text + structuredContent); Notes: 系统级入口, 禁止用 click/tap 模拟。"""
        return await broadcast(
            tool="open_menu",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(82, longpress)
        )

    @mcp.tool()
    @task_middleware("press_power")
    async def press_power(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 电源键(keycode=26); Args: longpress(bool)=False; Use: 锁屏/电源菜单; Return: CallToolResult(text + structuredContent); Notes: longpress 仅在用户明确“长按电源”时使用。"""
        return await broadcast(
            tool="press_power",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(26, longpress)
        )

    @mcp.tool()
    @task_middleware("press_enter")
    async def press_enter(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 回车确认(keycode=66); Args: longpress(bool)=False; Use: 提交/确认/默认操作; Return: CallToolResult(text + structuredContent); Notes: 依赖输入焦点。"""
        return await broadcast(
            tool="press_enter",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(66, longpress)
        )

    @mcp.tool()
    @task_middleware("press_tab")
    async def press_tab(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 焦点切换(keycode=61); Args: longpress(bool)=False; Use: 表单输入流切换输入框; Return: CallToolResult(text + structuredContent); Notes: 依赖页面可聚焦控件。"""
        return await broadcast(
            tool="press_tab",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(61, longpress)
        )

    @mcp.tool()
    @task_middleware("press_delete")
    async def press_delete(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 删除字符(keycode=67); Args: longpress(bool)=False; Use: 删除/修正输入; Return: CallToolResult(text + structuredContent); Notes: 依赖输入焦点。"""
        return await broadcast(
            tool="press_delete",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(67, longpress)
        )

    @mcp.tool()
    @task_middleware("press_space")
    async def press_space(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 输入空格(keycode=62); Args: longpress(bool)=False; Use: 插入空格/分隔文本; Return: CallToolResult(text + structuredContent); Notes: 依赖输入焦点。"""
        return await broadcast(
            tool="press_space",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(62, longpress)
        )

    @mcp.tool()
    @task_middleware("press_escape")
    async def press_escape(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 取消/退出编辑(keycode=111); Args: longpress(bool)=False; Use: 取消输入/退出编辑态; Return: CallToolResult(text + structuredContent); Notes: 部分应用等价“取消/返回”。"""
        return await broadcast(
            tool="press_escape",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(111, longpress)
        )

    @mcp.tool()
    @task_middleware("press_voice_assist")
    async def press_voice_assist(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 语音助手(keycode=231); Args: longpress(bool)=False; Use: 唤起系统语音助理; Return: CallToolResult(text + structuredContent); Notes: 系统级快捷入口, 禁止 click/tap."""
        return await broadcast(
            tool="press_voice_assist",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(231, longpress)
        )

    @mcp.tool()
    @task_middleware("volume_up")
    async def volume_up(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 音量加(keycode=24); Args: longpress(bool)=False; Use: 调高音量; Return: CallToolResult(text + structuredContent); Notes: 系统级按键。"""
        return await broadcast(
            tool="volume_up",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(24, longpress)
        )

    @mcp.tool()
    @task_middleware("volume_down")
    async def volume_down(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 音量减(keycode=25); Args: longpress(bool)=False; Use: 调低音量; Return: CallToolResult(text + structuredContent); Notes: 系统级按键。"""
        return await broadcast(
            tool="volume_down",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(25, longpress)
        )

    @mcp.tool()
    @task_middleware("volume_mute")
    async def volume_mute(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 静音(keycode=164); Args: longpress(bool)=False; Use: 快速静音; Return: CallToolResult(text + structuredContent); Notes: 系统级按键。"""
        return await broadcast(
            tool="volume_mute",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(164, longpress)
        )

    @mcp.tool()
    @task_middleware("media_play_pause")
    async def media_play_pause(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 播放/暂停(keycode=85); Args: longpress(bool)=False; Use: 控制系统媒体播放; Return: CallToolResult(text + structuredContent); Notes: 不依赖当前应用 UI."""
        return await broadcast(
            tool="media_play_pause",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(85, longpress)
        )

    @mcp.tool()
    @task_middleware("media_next")
    async def media_next(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 下一首(keycode=87); Args: longpress(bool)=False; Use: 切歌/切换媒体; Return: CallToolResult(text + structuredContent); Notes: 系统媒体控制。"""
        return await broadcast(
            tool="media_next",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(87, longpress)
        )

    @mcp.tool()
    @task_middleware("media_previous")
    async def media_previous(longpress: bool = False) -> CallToolResult:
        """Class: system; Action: 上一首(keycode=88); Args: longpress(bool)=False; Use: 返回上一条媒体; Return: CallToolResult(text + structuredContent); Notes: 系统媒体控制。"""
        return await broadcast(
            tool="media_previous",
            args={"longpress": longpress},
            target_list=manage.snapshot,
            call=lambda agent: agent.key_event(88, longpress)
        )


if __name__ == '__main__':
    pass
