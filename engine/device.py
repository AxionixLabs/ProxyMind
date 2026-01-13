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
from utils import (
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
        """构建当前设备的 adb 命令前缀。"""
        return ["adb", "-s", self.serial]

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

    async def st_battery(self) -> int | None:
        """读取电池 scale 数值。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "battery"
        ]
        resp = await Terminal.cmd_line(cmd)

        return m.group() if (m := re.search(r"(?<=scale:\s)\d+", resp, re.S)) else None

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

    async def is_screen_on(self) -> bool:
        """检查屏幕是否处于点亮状态。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "power", "|", "grep", "mWakefulness"
        ]
        return "Awake" in await Terminal.cmd_line(cmd)

    async def is_screen_lock(self) -> bool:
        """检查是否正在显示锁屏。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mDreamingLockscreen"
        ]
        return "Awake" in await Terminal.cmd_line(cmd)

    async def is_emulator(self) -> bool:
        """根据硬件/机型特征判断是否为模拟器。"""
        return (
            "goldfish" in self.hardware or "ranchu" in self.hardware or "sdk" in self.model
        )

    # workflow: ==== MCP Tool ====
    async def swipe_unlock(self) -> None:
        """点亮屏幕并上滑解锁。"""

        # 1️⃣ 点亮屏幕
        if not await self.is_screen_on():
            await self.key_event(26)
            await asyncio.sleep(0.2)

        # 2️⃣ 获取屏幕尺寸
        w, h = await self.st_wm_size()

        # 3️⃣ 计算上滑路径（符合人类手势）
        x = w // 2
        y1 = int(h * 0.80)
        y2 = int(h * 0.35)

        # 4️⃣ 执行滑动解锁
        await self.swipe(x, y1, x, y2, 1000)

        # 5️⃣ 稳定等待
        await asyncio.sleep(0.2)

    # workflow: ==== MCP Tool ====
    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> typing.Any:
        """从起点滑动到终点。"""
        cmd = self.prefix + [
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== MCP Tool ====
    async def tap(self, x: int, y: int) -> typing.Any:
        """点击指定坐标。"""
        cmd = self.prefix + [
            "shell", "input", "tap", str(x), str(y)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== MCP Tool ====
    async def key_event(self, keycode: int) -> typing.Any:
        """发送 Android 按键事件。"""
        cmd = self.prefix + [
            "shell", "input", "keyevent", str(keycode)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== MCP Tool ====
    async def click(self, by: typing.Literal["text", "id", "desc"], value: str | list) -> typing.Any:
        """根据选择器点击对应节点中心点。"""
        if not (xml := await self.dump_ui_xml()):
            return None

        if by == "bbox":
            x1, y1, x2, y2 = value
            center = (x1 + x2) // 2, (y1 + y2) // 2
            return await self.tap(center[0], center[1])

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

    # workflow: ==== MCP Tool ====
    async def send_keys(self, text: str) -> typing.Any:
        """向当前焦点输入文本。"""
        cmd = self.prefix + [
            "shell", "input", "text", text
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== MCP Tool ====
    async def combo_key(self, first: int, *others: int) -> typing.Any:
        """组合按键执行。"""
        if not others: return None

        commands = [f"input keyevent {other}" for other in others]

        shell_cmd = " ".join(
            self.prefix + ["shell", "input", "keyevent"]
        ) + f" --longpress {first} & sleep 0.03; " + "; ".join(commands)

        return await Terminal.cmd_line_shell(shell_cmd)

    # workflow: ==== MCP Tool ====
    async def deep_link(self, url: str) -> typing.Any:
        """通过深度链接启动指定的应用服务。"""
        cmd = self.prefix + [
            "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url
        ]
        return await Terminal.cmd_line_shell(" ".join(cmd))

    # workflow: ==== MCP Tool ====
    async def app_start(self, package: str) -> typing.Any:
        """启动指定包名的应用。"""
        cmd = self.prefix + [
            "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== MCP Tool ====
    async def force_stop(self, package: str) -> typing.Any:
        """强制停止指定包名的应用。"""
        cmd = self.prefix + [
            "shell", "am", "force-stop", package
        ]
        return await Terminal.cmd_line(cmd)

    async def current_activity(self) -> str | None:
        """获取当前前台 Activity 标识。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"
        ]

        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        if match := re.search(r"([a-zA-Z0-9._]+/[a-zA-Z0-9._$]+)", resp):
            return match.group(1)

        if match := re.search(r"\bu\d+\s+([a-zA-Z0-9._]+)\b", resp):
            return match.group(1)

        return None

    async def dump_ui_xml(self) -> str | None:
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

    async def screenshot(self) -> str:
        """在设备上截屏并返回远端路径。"""
        filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        remote   = f"/data/local/tmp/{filename}"

        cmd = self.prefix + [
            "shell", "screencap", "-p", remote
        ]
        await Terminal.cmd_line(cmd)

        return remote

    async def pull(self, remote: str, local: str) -> dict:
        """从设备拉取文件到本地。"""
        cmd = self.prefix + [
            "pull", remote, local
        ]
        return await Terminal.cmd_line(cmd)

    async def push(self, local: str, remote: str) -> dict:
        """将本地文件推送到设备。"""
        cmd = self.prefix + [
            "push", local, remote
        ]
        return await Terminal.cmd_line(cmd)

    async def remove(self, remote: str) -> dict:
        """删除设备上的文件。"""
        cmd = self.prefix + [
            "shell", "rm", "-f", remote
        ]
        return await Terminal.cmd_line(cmd)

    async def healing(self, by: typing.Literal["text", "id", "desc", "xpath"], value: str) -> None:
        """执行自愈流程定位并处理目标控件。"""
        page_id   = await self.current_activity() or ""
        platform  = "android"
        by        = by
        value     = value
        page_dump = await self.dump_ui_xml()

        if not page_dump: return None

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image = await self.screenshot()
            await self.pull(image, tmp.name)

            async for _ in request.stream_self_heal(page_id, platform, by, value, page_dump, tmp.name):
                 pass

            await self.remove(image)


if __name__ == '__main__':
    pass
