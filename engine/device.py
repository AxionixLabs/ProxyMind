#  ____             _
# |  _ \  _____   _(_) ___ ___
# | | | |/ _ \ \ / / |/ __/ _ \
# | |_| |  __/\ V /| | (_|  __/
# |____/ \___| \_/ |_|\___\___|
#

import re
import time
import uuid
import typing
import asyncio
import tempfile
import xml.etree.ElementTree as Et
from engine.terminal import Terminal
from mindnova import (
    const, request
)


class Device(object):

    def __init__(self, serial: str):
        self.serial = serial

        self.brand    : str | None = None
        self.model    : str | None = None
        self.version  : str | None = None
        self.hardware : str | None = None
        self.sdk      : str | None = None
        self.abi      : str | None = None

        self.locale   : str | None = None
        self.timezone : str | None = None

        self.debuggable : bool | None = None
        self.secure     : bool | None = None

    def __str__(self):
        return (
            f"<Device {self.brand} {self.model} "
            f"serial={self.serial} version={self.version} hardware={self.hardware} sdk={self.sdk} abi={self.abi} "
            f"locale={self.locale} timezone={self.timezone} debuggable={self.debuggable} secure={self.secure}>"
        )

    __repr__ = __str__

    @property
    def prefix(self) -> list[str]:
        return ["adb", "-s", self.serial]

    @property
    def device_info(self) -> dict:
        return {
            "serial"     : self.serial,
            "brand"      : self.brand,
            "model"      : self.model,
            "version"    : self.version,
            "hardware"   : self.hardware,
            "sdk"        : self.sdk,
            "abi"        : self.abi,
            "locale"     : self.locale,
            "timezone"   : self.timezone,
            "debuggable" : self.debuggable,
            "secure"     : self.secure
        }
        
    # workflow: ==== Device Info MCP Tool ====
    async def snapshot(self) -> dict:
        """采集并返回该设备当前所有状态快照。"""
        battery     = await self.st_battery()
        wm_size     = await self.st_wm_size()
        online      = await self.is_online()
        emulator    = await self.is_emulator()
        screen_lock = await self.is_screen_lock()

        return self.device_info | {
            "battery"     : battery,
            "wm_size"     : {"w": wm_size[0], "h": wm_size[1]} if wm_size else None,
            "online"      : online,
            "emulator"    : emulator,
            "screen_lock" : screen_lock
        }
        
    # workflow: ==== Device ====
    async def st_load_info(self) -> None:
        """从 adb getprop 加载并填充设备属性。"""
        if not (resp := await Terminal.cmd_line(self.prefix + ["shell", "getprop"])):
            return None

        pick: typing.Callable[
            [str], str
        ] = lambda x: m.group(1) if (m := re.search(rf"\[{re.escape(x)}]: \[(.*?)]", resp)) else "Unknown"

        self.brand    = pick("ro.product.brand")
        self.model    = pick("ro.product.model")
        self.version  = pick("ro.build.version.release")
        self.hardware = pick("ro.hardware")
        self.sdk      = pick("ro.build.version.sdk")
        self.abi      = pick("ro.product.cpu.abi")

        self.locale   = pick("persist.sys.locale")
        self.timezone = pick("persist.sys.timezone")

        self.debuggable = pick("ro.debuggable") == "1"
        self.secure     = pick("ro.secure")     == "1"

    # workflow: ==== Device ====
    async def st_battery(self) -> int | None:
        """读取电池 scale 数值。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "battery"
        ]
        resp = await Terminal.cmd_line(cmd)

        return m.group() if (m := re.search(r"(?<=scale:\s)\d+", resp, re.S)) else None

    # workflow: ==== Device ====
    async def st_wm_size(self) -> tuple[int, int] | None:
        """获取物理屏幕分辨率。"""
        cmd = self.prefix + [
            "shell", "wm", "size"
        ]
        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        if not (m := re.search(r"Physical size:\s*(\d+)x(\d+)", resp)):
            return None

        return int(m.group(1)), int(m.group(2))
    
    # workflow: ==== Device ====
    async def is_online(self) -> bool:
        """是否能真正访问互联网。"""
        resp = await Terminal.cmd_line(
            self.prefix + ["shell", "ping", "-c", "1", "8.8.8.8"]
        )
        return bool(resp and "1 packets transmitted" in resp)

    # workflow: ==== Device ====
    async def is_emulator(self) -> bool:
        """根据硬件/机型特征判断是否为模拟器。"""
        return (
            "goldfish" in self.hardware or "ranchu" in self.hardware or "sdk" in self.model
        )

    # workflow: ==== Device ====
    async def is_screen_lock(self) -> bool:
        """检查是否正在显示锁屏。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mDreamingLockscreen"
        ]
        return "Awake" in await Terminal.cmd_line(cmd)

    # workflow: ==== Device ====
    async def is_screen_on(self) -> bool:
        """检查屏幕是否处于点亮状态。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "power", "|", "grep", "mWakefulness"
        ]
        return "Awake" in await Terminal.cmd_line(cmd)

    # workflow: ==== App Control MCP Tool ====
    async def deep_link(self, url: str) -> typing.Any:
        """通过深度链接启动指定的应用服务。"""
        cmd = self.prefix + [
            "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url
        ]
        return await Terminal.cmd_line_shell(" ".join(cmd))

    # workflow: ==== App Control MCP Tool ====
    async def app_start(self, package: str, activity: typing.Optional[str] = None) -> typing.Any:
        """启动指定 Android 应用，可选择精确启动 Activity 或默认 Launcher 入口。"""
        action, category = "android.intent.action.MAIN", "android.intent.category.LAUNCHER"

        if activity:
            cmd = self.prefix + [
                "am", "start", "-a", action, "-c", category, "-n", f"{package}/{activity}"
            ]
            return await Terminal.cmd_line(cmd)

        cmd = self.prefix + [
            "shell", "monkey", "-p", package, "-c", category, "1"
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== App Control MCP Tool ====
    async def app_stop(self, package: str) -> typing.Any:
        """强制停止指定包名的应用。"""
        cmd = self.prefix + [
            "shell", "am", "force-stop", package
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== App Control MCP Tool ====
    async def app_install(self, apk: str, replace: bool = True, downgrade: bool = False, test: bool = False) -> typing.Any:
        """安装 APK。"""
        cmd = self.prefix + ["install"]

        if replace: cmd.append("-r")
        if downgrade: cmd.append("-d")
        if test: cmd.append("-t")

        cmd.append(apk)

        return await Terminal.cmd_line(cmd)

    # workflow: ==== App Control MCP Tool ====
    async def app_uninstall(self, package: str, *, keep_data: bool = False) -> typing.Any:
        """卸载指定包名的应用。"""
        cmd = self.prefix + [
            "shell", "pm", "uninstall"
        ]

        if keep_data: cmd.append("-k")

        cmd.append(package)

        return await Terminal.cmd_line(cmd)

    # workflow: ==== App Control MCP Tool ====
    async def app_clear(self, package: str) -> typing.Any:
        """清除指定应用的数据与缓存（等价于系统设置中的“清除数据”）。"""
        cmd = self.prefix + [
            "shell", "pm", "clear", package
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== File Control MCP Tool ====
    async def pull(self, remote: str, local: str) -> typing.Any:
        """从设备拉取文件到本地。"""
        cmd = self.prefix + [
            "pull", remote, local
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== File Control MCP Tool ====
    async def push(self, local: str, remote: str) -> typing.Any:
        """将本地文件推送到设备。"""
        cmd = self.prefix + [
            "push", local, remote
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== File Control MCP Tool ====
    async def remove(self, path: str) -> typing.Any:
        """删除设备上的文件。"""
        cmd = self.prefix + [
            "shell", "rm", "-f", path
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== Media Control MCP Tool ====
    async def screenshot(self) -> str:
        """在设备上截屏并返回远端路径。"""
        filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        remote   = f"/data/local/tmp/{filename}"

        cmd = self.prefix + [
            "shell", "screencap", "-p", remote
        ]
        await Terminal.cmd_line(cmd)

        return remote

    # workflow: ==== System Control MCP Tool ====
    async def open_notification(self) -> typing.Any:
        """打开通知栏（Notification Panel）。"""
        cmd = self.prefix + [
            "shell", "cmd", "statusbar", "expand-notifications"
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System Control MCP Tool ====
    async def open_quick_settings(self) -> typing.Any:
        """打开快速设置面板（Quick Settings Panel）。"""
        cmd = self.prefix + [
            "shell", "cmd", "statusbar", "expand-settings"
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System Control MCP Tool ====
    async def combo_key(self, first: int, *others: int) -> typing.Any:
        """组合按键执行。"""
        if not others: return None

        commands = [f"input keyevent {other}" for other in others]

        shell_cmd = " ".join(
            self.prefix + ["shell", "input", "keyevent"]
        ) + f" --longpress {first} & sleep 0.03; " + "; ".join(commands)

        return await Terminal.cmd_line_shell(shell_cmd)

    # workflow: ==== System Control MCP Tool ====
    async def swipe_unlock(self) -> None:
        """点亮屏幕并上滑解锁。"""
        await self.screen_set(True)

        w, h = await self.st_wm_size()

        x = w // 2
        y1 = int(h * 0.80)
        y2 = int(h * 0.35)

        await self.swipe(x, y1, x, y2, 1000)
        await asyncio.sleep(0.2)

    # workflow: ==== System Control MCP Tool ====
    async def screen_on(self) -> None:
        """点亮屏幕。"""
        return await self.screen_set(True)

    # workflow: ==== System Control MCP Tool ====
    async def screen_off(self) -> None:
        """熄屏锁屏。"""
        return await self.screen_set(False)

    # workflow: ==== System ====
    async def screen_set(self, on: bool, settle: float = 0.2) -> None:
        """统一控制屏幕电源态。"""
        if on == await self.is_screen_on():
            return None

        await self.key_event(26)
        await asyncio.sleep(settle)

    # workflow: ==== System ====
    async def bluetooth_set(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """控制蓝牙状态。"""
        cmd = self.prefix + [
            "shell", "svc", "bluetooth", status
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System ====
    async def wifi_set(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """控制 WiFi 状态。"""
        cmd = self.prefix + [
            "shell", "svc", "wifi", status
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System ====
    async def data_set(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """控制移动数据状态。"""
        cmd = self.prefix + [
            "shell", "svc", "data", status
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System ====
    async def key_event(self, keycode: int, longpress: bool = False) -> typing.Any:
        """向设备发送 Android 系统按键事件（支持普通按键与长按）。"""
        cmd = self.prefix + [
            "shell", "input", "keyevent"
        ]
        if longpress: cmd += ["--longpress"]
        cmd += [str(keycode)]

        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> typing.Any:
        """从起点滑动到终点。"""
        cmd = self.prefix + [
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def swipe_direction(
        self,
        direction: typing.Literal["up", "down", "left", "right"],
        x: int,
        y: int,
        duration: int = 300,
    ) -> typing.Any:
        """以锚点为参考，按方向进行语义滑动，根据屏幕尺寸自动计算终点坐标。"""
        w, h = await self.st_wm_size()

        x1, y1, x2, y2 = x, y, 0, 0

        match direction:
            case "up"    : x2, y2 = x1, max(0, int(h * 0.25))
            case "down"  : x2, y2 = x1, min(h - 1, int(h * 0.75))
            case "left"  : x2, y2 = max(0, int(w * 0.25)), y1
            case "right" : x2, y2 = min(w - 1, int(w * 0.75)), y1

        cmd = self.prefix + [
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def tap(self, x: int, y: int) -> typing.Any:
        """点击指定坐标。"""
        cmd = self.prefix + [
            "shell", "input", "tap", str(x), str(y)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def click(self, by: typing.Literal["text", "id", "desc"], value: str | list) -> typing.Any:
        """根据选择器点击对应节点中心点。"""
        if not (xml := await self.current_xml()):
            return None

        match by:
            case "id": by = "resource-id"
            case "desc": by = "content-desc"

        node = None
        for n in Et.fromstring(xml).iter("node"):
            if n.attrib.get(by) == value:
                node = n.attrib; break

        if not node or not (bounds := node.get("bounds")):
            return None

        match = re.match(r"\[(\d+),(\d+)]\[(\d+),(\d+)]", bounds)
        x1, y1, x2, y2 = map(int, match.groups())
        center = (x1 + x2) // 2, (y1 + y2) // 2

        return await self.tap(center[0], center[1])

    # workflow: ==== UI Interaction MCP Tool ====
    async def double_click(self, x: int, y: int) -> typing.Any:
        """在同一坐标执行双击（两次 tap，中间等待 0.08 秒）。"""
        cmd = (
            " ".join(self.prefix)
            + f" shell input tap {x} {y}; sleep 0.08; input tap {x} {y}"
        )
        return await Terminal.cmd_line_shell(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def send_keys(self, text: str) -> typing.Any:
        """向当前焦点输入文本。"""
        cmd = self.prefix + [
            "shell", "input", "text", text
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_package(self) -> str | None:
        """获取当前前台应用包名。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"
        ]

        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        # 1) 优先从 package/activity 提取 package
        if m := re.search(r"([a-zA-Z0-9._]+)/[a-zA-Z0-9._$]+", resp):
            return m.group(1)

        # 2) 退化：从 "u0 com.xxx.app" 这类结构提取 package
        if m := re.search(r"\bu\d+\s+([a-zA-Z0-9._]+)\b", resp):
            return m.group(1)

        return None

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_activity(self) -> str | None:
        """获取当前前台 Activity 标识。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"
        ]

        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        # 1) 优先从 package/activity 提取 activity
        if match := re.search(r"([a-zA-Z0-9._]+/[a-zA-Z0-9._$]+)", resp):
            return match.group(1)

        # 2) 退化：从 "u0 com.xxx.app" 这类结构提取 package
        if match := re.search(r"\bu\d+\s+([a-zA-Z0-9._]+)\b", resp):
            return match.group(1)

        return None

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_xml(self) -> str | None:
        """导出当前 UI 层级 XML。"""
        xml_file = "/data/local/tmp/window_dump.xml"

        cmd = self.prefix + ["shell", "uiautomator", "dump", "--compressed", xml_file]
        await Terminal.cmd_line(cmd)

        await asyncio.sleep(1)

        cmd = self.prefix + ["shell", "cat", xml_file]
        for _ in range(5):
            xml = await Terminal.cmd_line(cmd)

            if isinstance(xml, bytes):
                xml = xml.decode(const.CHARSET, const.IGNORE)
            if xml and "<hierarchy" in xml:
                return xml
            await asyncio.sleep(0.2)

        return None

    async def healing(self, old_by: typing.Literal["text", "id", "desc", "xpath"], old_value: str) -> None:
        """执行自愈流程定位并处理目标控件。"""
        platform  = "android"
        page_id   = await self.current_activity() or ""
        page_dump = await self.current_xml() or ""

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            await self.pull(image := await self.screenshot(), tmp.name)

            async for _ in request.stream_self_heal(page_id, platform, old_by, old_value, page_dump, tmp.name):
                pass

            await self.remove(image)


if __name__ == '__main__':
    pass
