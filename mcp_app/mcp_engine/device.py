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
import xml.etree.ElementTree as Et
from engine.terminal import Terminal
from utils import const


class Device(object):

    def __init__(self, serial: str):
        self.serial = serial

        self.brand        : str | None = None
        self.model        : str | None = None
        self.manufacturer : str | None = None
        self.sdk          : str | None = None
        self.version      : str | None = None

    def __str__(self):
        return (
            f"<Device {self.brand} "
            f"serial={self.serial} "
            f"manufacturer={self.manufacturer} "
            f"sdk={self.sdk} "
            f"version={self.version}>"
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
        ] = lambda x: m if (m := re.search(rf"\[{re.escape(x)}]: \[(.*?)]", resp)) else "Unknown"

        self.brand        = pick("ro.product.brand")
        self.model        = pick("ro.product.model")
        self.manufacturer = pick("ro.product.manufacturer")
        self.sdk          = pick("ro.build.version.sdk")
        self.version      = pick("ro.build.version.release")

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


if __name__ == '__main__':
    pass
