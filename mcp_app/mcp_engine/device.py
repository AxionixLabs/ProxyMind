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
        return ["adb", "-s", self.serial]

    async def load_info(self) -> None:
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
        cmd = self.prefix + [
            "shell", "dumpsys", "battery"
        ]
        resp = await Terminal.cmd_line(cmd)

        return m.group() if (m := re.search(r"(?<=scale:\s)\d+", resp, re.S)) else None

    async def st_wm_size(self) -> tuple[int, int] | None:
        cmd = self.prefix + [
            "shell", "wm", "size"
        ]
        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        if not (m := re.search(r"Physical size:\s*(\d+)x(\d+)", resp)):
            return None

        return int(m.group(1)), int(m.group(2))

    async def is_screen_on(self) -> bool:
        cmd = self.prefix + [
            "shell", "dumpsys", "power", "|", "grep", "mWakefulness"
        ]
        return "Awake" in await Terminal.cmd_line(cmd)

    async def is_screen_lock(self) -> bool:
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mDreamingLockscreen"
        ]
        return "Awake" in await Terminal.cmd_line(cmd)

    async def is_emulator(self) -> bool:
        return (
            "goldfish" in self.hardware or "ranchu" in self.hardware or "sdk" in self.model
        )

    async def swipe_unlock(self) -> None:
        if not await self.is_screen_on():
            await self.key_event(26)
            await asyncio.sleep(0.2)

        w, h = await self.st_wm_size()

        x = w // 2
        y1 = int(h * 0.80)
        y2 = int(h * 0.35)

        await self.swipe(x, y1, x, y2, 1000)
        await asyncio.sleep(0.2)

    # workflow: ==== MCP Tool ====
    async def tap(self, x: int, y: int) -> typing.Any:
        cmd = self.prefix + [
            "shell", "input", "tap", str(x), str(y)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== MCP Tool ====
    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> typing.Any:
        cmd = self.prefix + [
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== MCP Tool ====
    async def key_event(self, keycode: int) -> typing.Any:
        cmd = self.prefix + [
            "shell", "input", "keyevent", str(keycode)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== MCP Tool ====
    async def click(self, by: typing.Literal["text", "id", "desc"], value: str) -> typing.Any:
        if not (xml := await self.dump_ui_xml()):
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

    # workflow: ==== MCP Tool ====
    async def send_keys(self, text: str) -> typing.Any:
        cmd = self.prefix + [
            "shell", "input", "text", text
        ]
        return await Terminal.cmd_line(cmd)

    async def current_activity(self) -> str | None:
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
        filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        remote   = f"/data/local/tmp/{filename}"

        cmd = self.prefix + [
            "shell", "screencap", "-p", remote
        ]
        await Terminal.cmd_line(cmd)

        return remote

    async def pull(self, remote: str, local: str) -> dict:
        cmd = self.prefix + [
            "pull", remote, local
        ]
        return await Terminal.cmd_line(cmd)

    async def push(self, local: str, remote: str) -> dict:
        cmd = self.prefix + [
            "push", local, remote
        ]
        return await Terminal.cmd_line(cmd)

    async def remove(self, remote: str) -> dict:
        cmd = self.prefix + [
            "shell", "rm", "-f", remote
        ]
        return await Terminal.cmd_line(cmd)

    async def self_heal(self, by: typing.Literal["text", "id", "desc", "xpath", "bbox"], value: str) -> None:
        page_id   = await self.current_activity() or ""
        platform  = "android"
        by        = by
        value     = value
        page_dump = await self.dump_ui_xml()

        if not page_dump: return None

        print(page_id)
        print(platform)
        print(by)
        print(value)
        print(page_dump)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            remote = await self.screenshot()
            await self.pull(remote, tmp.name)
            print("temp_file:", tmp.name)

            async for _ in request.stream_self_heal(page_id, platform, by, value, page_dump, tmp.name):
                 pass

            await self.remove(remote)


if __name__ == '__main__':
    pass
