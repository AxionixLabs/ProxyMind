#  ____  _
# |  _ \| |__   ___  _ __   ___
# | |_) | '_ \ / _ \| '_ \ / _ \
# |  __/| | | | (_) | | | |  __/
# |_|   |_| |_|\___/|_| |_|\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import time
import uuid
import typing
import asyncio
import contextlib
import xml.etree.ElementTree as Et
from pathlib import Path
from .widget import Widget
from backend.utilities import const
from engine.terminal import Terminal


class Phone(object):
    """设备原子能力层。"""

    def __init__(self, serial: str):
        self.serial = serial

        self.agent_id: str = self.serial

        self._device_props_cache: dict[str, typing.Any] = {
            "serial"     : self.serial,
            "brand"      : None,
            "model"      : None,
            "version"    : None,
            "hardware"   : None,
            "sdk"        : None,
            "abi"        : None,
            "locale"     : None,
            "timezone"   : None,
            "debuggable" : None,
            "secure"     : None
        }

    @property
    def prefix(self) -> list[str]:
        """生成统一的 adb 前缀。"""
        return ["adb", "-s", self.serial]

    @property
    def device_props(self) -> dict[str, typing.Any]:
        return dict(self._device_props_cache)

    def _sync_device_props_cache(self, props: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """刷新设备属性缓存。"""
        cached = dict(self._device_props_cache)
        cached.update(props or {})
        cached["serial"] = self.serial
        self._device_props_cache = cached

        return dict(cached)

    @staticmethod
    def _pick_prop(resp: str, key: str, default: str = "unknown") -> str:
        if m := re.search(rf"\[{re.escape(key)}]: \[(.*?)]", resp):
            return m.group(1)
        return default

    @classmethod
    def _parse_device_props(cls, resp: str, serial: str) -> dict[str, typing.Any]:
        """解析设备属性文本。"""
        return {
            "serial"     : serial,
            "brand"      : cls._pick_prop(resp, "ro.product.brand"),
            "model"      : cls._pick_prop(resp, "ro.product.model"),
            "version"    : cls._pick_prop(resp, "ro.build.version.release"),
            "hardware"   : cls._pick_prop(resp, "ro.hardware"),
            "sdk"        : cls._pick_prop(resp, "ro.build.version.sdk"),
            "abi"        : cls._pick_prop(resp, "ro.product.cpu.abi"),
            "locale"     : cls._pick_prop(resp, "persist.sys.locale"),
            "timezone"   : cls._pick_prop(resp, "persist.sys.timezone"),
            "debuggable" : cls._pick_prop(resp, "ro.debuggable") == "1",
            "secure"     : cls._pick_prop(resp, "ro.secure") == "1",
        }

    @staticmethod
    def _parse_focus(raw: str) -> dict[str, typing.Optional[str] | str]:
        """解析前台焦点信息。"""
        raw = str(raw or "").strip()
        package: typing.Optional[str] = None
        activity: typing.Optional[str] = None

        if m := re.search(r"([a-zA-Z0-9._]+/[a-zA-Z0-9._$]+)", raw):
            activity = m.group(1)
            package = activity.split("/", 1)[0]
        elif m := re.search(r"\bu\d+\s+([a-zA-Z0-9._]+)\b", raw):
            package = m.group(1)

        return {
            "package"  : package,
            "activity" : activity,
            "raw"      : raw
        }

    @staticmethod
    def _normalize_xml(xml: str) -> str:
        return re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", xml or "")

    @classmethod
    def _parse_widgets(cls, xml: str) -> list[Widget]:
        """解析页面控件列表。"""
        try:
            root = Et.fromstring(cls._normalize_xml(xml))
        except (Et.ParseError, TypeError):
            return []

        return [
            Widget(node.attrib) for node in root.iter("node")
        ]

    @staticmethod
    def _match_widget(
        widget_list: list[Widget],
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> typing.Optional[Widget]:
        """匹配首个命中的控件。"""
        if by == "xpath":
            return None

        if by == "bbox":
            if not isinstance(value, (list, tuple)):
                return None
            for widget in widget_list:
                if widget.bbox == list(value):
                    return widget
            return None

        if not isinstance(value, str):
            return None

        needle = value.lower() if ignore_case else value
        pattern: typing.Optional[re.Pattern[str]] = None

        if match == "regex":
            flags = re.IGNORECASE if ignore_case else 0
            try:
                pattern = re.compile(value, flags)
            except re.error:
                return None

        for widget in widget_list:
            got = getattr(widget, by, "")
            hay = got.lower() if ignore_case else got

            if match == "eq":
                ok = (hay == needle) if ignore_case else (got == value)
            elif match == "contains":
                ok = needle in hay
            else:
                ok = bool(pattern.search(got)) if pattern else False

            if ok:
                return widget

        return None

    async def refresh_device_props(self) -> dict[str, typing.Any]:
        """刷新设备属性缓存。"""
        cmd = self.prefix + [
            "shell", "getprop"
        ]
        if not (resp := await Terminal.cmd_line(cmd)):
            return self._sync_device_props_cache({})

        return self._sync_device_props_cache(self._parse_device_props(resp, self.serial))

    async def battery(self) -> int | None:
        """读取电池电量百分比。"""
        cmd = self.prefix + ["shell", "dumpsys", "battery"]
        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        m_level = re.search(r"(?m)^\s*level:\s*(\d+)\s*$", resp)
        m_scale = re.search(r"(?m)^\s*scale:\s*(\d+)\s*$", resp)
        if not (m_level and m_scale):
            return None

        level = int(m_level.group(1))
        scale = int(m_scale.group(1)) or 100

        return int(round(level * 100 / scale))

    async def wm_size(self) -> tuple[int, int] | None:
        """获取物理屏幕分辨率。"""
        cmd = self.prefix + [
            "shell", "wm", "size"
        ]
        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        if not (m := re.search(r"Physical size:\s*(\d+)x(\d+)", resp)):
            return None

        return int(m.group(1)), int(m.group(2))

    async def is_online(self) -> bool:
        """检查设备是否联网。"""
        resp = await Terminal.cmd_line(
            self.prefix + ["shell", "ping", "-c", "1", "1.1.1.1"]
        )
        return bool(resp and "1 packets transmitted" in resp)

    async def is_emulator(self) -> bool:
        """检查是否为模拟器。"""
        props = self.device_props
        hardware = str(props.get("hardware") or "").lower()
        model = str(props.get("model") or "").lower()
        return (
            "goldfish" in hardware or "ranchu" in hardware or "sdk" in model
        )

    async def is_screen_locked(self) -> bool:
        """检查是否处于锁屏状态。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "policy", "|", "grep", "mInputRestricted"
        ]
        resp = await Terminal.cmd_line(cmd)
        return bool(resp and "true" in resp)

    async def is_screen_on(self) -> bool:
        """检查屏幕是否点亮。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "deviceidle", "|", "grep", "mScreenOn"
        ]
        resp = await Terminal.cmd_line(cmd)
        return bool(resp and "true" in resp)

    async def screencap(self, remote: str) -> typing.Any:
        """执行设备端截图。"""
        capture = self.prefix + ["shell", "screencap", "-p", remote]
        return await Terminal.cmd_line(capture)

    async def save_screenshot(self, local: str) -> str:
        """保存截图到本地路径。"""
        # 远端文件名每次唯一，避免并发截图时互相覆盖。
        filename = f"screenshot_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        remote = "/data/local/tmp/" + filename

        await self.screencap(remote)

        if (p := Path(local)).suffix:
            destination = p.with_name(f"{p.stem}_{self.serial}{p.suffix}")
        else:
            destination = p / f"screenshot_{self.serial}_{uuid.uuid4().hex[:6]}.png"

        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        pull = self.prefix + ["pull", remote, str(destination)]
        await Terminal.cmd_line(pull)

        remove = self.prefix + ["shell", "rm", "-f", remote]
        with contextlib.suppress(Exception):
            await Terminal.cmd_line(remove)

        # pull 成功与否不能只看命令返回，最终以本地文件是否存在为准。
        if not destination.exists():
            raise FileNotFoundError(f"screenshot pull failed: {destination}")

        return str(destination)

    async def file_logcat_link(self) -> asyncio.subprocess.Process:
        """连接 logcat 输出流。"""
        cmd = self.prefix + [
            "logcat", "-v", "threadtime"
        ]
        return await Terminal.cmd_link(cmd)

    async def send_keyevent(self, keycode: int, longpress: bool = False) -> typing.Any:
        """发送系统按键事件。"""
        cmd = self.prefix + [
            "shell", "input", "keyevent"
        ]
        if longpress: cmd += ["--longpress"]
        cmd += [str(keycode)]

        return await Terminal.cmd_line(cmd)

    async def set_bluetooth(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """设置蓝牙状态。"""
        cmd = self.prefix + [
            "shell", "svc", "bluetooth", status
        ]
        return await Terminal.cmd_line(cmd)

    async def set_wifi(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """设置 WiFi 状态。"""
        cmd = self.prefix + [
            "shell", "svc", "wifi", status
        ]
        return await Terminal.cmd_line(cmd)

    async def set_mobile_data(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        """设置移动数据状态。"""
        cmd = self.prefix + [
            "shell", "svc", "data", status
        ]
        return await Terminal.cmd_line(cmd)

    async def tap(self, x: int, y: int) -> typing.Any:
        """点击指定坐标。"""
        cmd = self.prefix + [
            "shell", "input", "tap", str(x), str(y)
        ]
        return await Terminal.cmd_line(cmd)

    async def focus_info(self) -> dict[str, typing.Optional[str] | str]:
        """获取当前前台焦点。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"
        ]

        if not (resp := await Terminal.cmd_line(cmd)):
            return {"package": None, "activity": None, "raw": ""}

        return self._parse_focus(str(resp))

    async def ui_xml(self) -> typing.Optional[str]:
        """导出当前页面 XML。"""
        xml_file = "/data/local/tmp/window_dump.xml"

        cmd = self.prefix + [
            "shell", "uiautomator", "dump", "--compressed", xml_file
        ]
        await Terminal.cmd_line(cmd)

        cat = self.prefix + ["shell", "cat", xml_file]
        remove = self.prefix + ["shell", "rm", "-f", xml_file]
        try:
            # uiautomator dump 生成文件有延迟，短轮询几次比一次性读取更稳。
            for _ in range(6):
                xml = await Terminal.cmd_line(cat)

                xml = xml.decode(const.CHARSET, const.IGNORE) if isinstance(
                    xml, (bytes, bytearray)
                ) else (xml or "")

                if "<hierarchy" in xml:
                    return xml
                await asyncio.sleep(0.12)
            return None
        finally:
            with contextlib.suppress(Exception):
                await Terminal.cmd_line(remove)

    async def ui_widgets(self) -> list[Widget]:
        """获取当前页面控件列表。"""
        if not (xml := await self.ui_xml()):
            return []

        return self._parse_widgets(xml)

    async def scroll_by_direction(
        self,
        direction: typing.Literal["up", "down", "left", "right"],
        x: int,
        y: int,
        duration: int = 300
    ) -> typing.Any:
        """按内容方向执行语义滑动。"""
        # 这里依赖真实分辨率做手势落点，拿不到屏幕尺寸时直接失败。
        if not (wm := await self.wm_size()):
            raise RuntimeError("wm_size unavailable")

        w, h = wm

        x1, y1 = x, y
        x2, y2 = x1, y1

        match direction:
            case "up":
                x2, y2 = x1, min(h - 1, int(h * 0.75))
            case "down":
                x2, y2 = x1, max(0, int(h * 0.25))
            case "left":
                x2, y2 = min(w - 1, int(w * 0.75)), y1
            case "right":
                x2, y2 = max(0, int(w * 0.25)), y1

        cmd = self.prefix + [
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        ]
        return await Terminal.cmd_line(cmd)

    async def find_ui_widget(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> typing.Optional[Widget]:
        """查找首个命中的页面控件。"""
        widget_list = await self.ui_widgets()
        return self._match_widget(widget_list, by, value, match, ignore_case)


if __name__ == '__main__':
    pass
