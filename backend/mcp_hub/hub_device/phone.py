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
    """原子能力层"""

    def __init__(self, serial: str):
        self.serial = serial

        self.agent_id: str = self.serial

        self.brand    : typing.Optional[str] = None
        self.model    : typing.Optional[str] = None
        self.version  : typing.Optional[str] = None
        self.hardware : typing.Optional[str] = None
        self.sdk      : typing.Optional[str] = None
        self.abi      : typing.Optional[str] = None

        self.locale   : typing.Optional[str] = None
        self.timezone : typing.Optional[str] = None

        self.debuggable : typing.Optional[bool] = None
        self.secure     : typing.Optional[bool] = None

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

    @staticmethod
    def device_semantics(device_snap: dict) -> dict:
        """将“设备快照 snap(dict)”转换为两种更适合大模型/检索的语义描述。"""

        def brief(v: typing.Optional[bool]) -> typing.Optional[str]:
            """结构化 KV 串（稳定、可做 embedding / recall 的输入）"""
            if v is None: return None
            return "true" if v else "false"

        def kv(items: list[tuple[str, typing.Any]]) -> str:
            """人类可读摘要（日志/终端展示友好）"""
            parts: list[str] = []
            for k, v in items:
                if v is None: continue
                if isinstance(v, str) and not v.strip(): continue
                parts.append(f"{k}={v}")
            return "; ".join(parts)

        wm     = device_snap.get("wm_size") or {}
        screen = f"{wm.get('w')}x{wm.get('h')}" if (wm.get("w") and wm.get("h")) else None

        battery   = device_snap.get("battery")
        battery_s = f"{battery}%" if battery is not None else None

        online    = device_snap.get("online") is True
        locked    = device_snap.get("screen_lock") is True
        screen_on = device_snap.get("screen_on") is True

        semantic_kv = kv([
            ("kind",       "device"),
            ("serial",     device_snap.get("serial")),
            ("brand",      device_snap.get("brand")),
            ("model",      device_snap.get("model")),
            ("android",    device_snap.get("version")),
            ("sdk",        device_snap.get("sdk")),
            ("abi",        device_snap.get("abi")),
            ("hw",         device_snap.get("hardware")),
            ("locale",     device_snap.get("locale")),
            ("tz",         device_snap.get("timezone")),
            ("screen",     screen),
            ("battery",    battery_s),
            ("online",     brief(online)),
            ("locked",     brief(locked)),
            ("screen_on",  brief(screen_on)),
            ("secure",     brief(device_snap.get("secure") is True)),
            ("debuggable", brief(device_snap.get("debuggable") is True)),
            ("emulator",   brief(device_snap.get("emulator") is True)),
        ])

        semantic_brief = (
            f"{device_snap.get('serial') or 'unknown'}: "
            f"{'在线' if online else '离线'} / "
            f"{'锁屏' if locked else '未锁屏'} / "
            f"{'亮屏' if screen_on else '灭屏'} / "
            f"电量{battery_s or 'unknown'} / "
            f"屏幕{screen.replace('x', '×') if screen else 'unknown'} / "
            f"Android{device_snap.get('version') or '?'}(SDK{device_snap.get('sdk') or '?'}) / "
            f"{(device_snap.get('brand') or '').strip()} {(device_snap.get('model') or '').strip()}".strip()
        )

        return {
            "snapshot"       : device_snap,
            "semantic_kv"    : semantic_kv,
            "semantic_brief" : semantic_brief
        }

    # workflow: ==== Info ====
    async def st_load_info(self) -> None:
        """从 adb getprop 加载并填充设备属性。"""
        cmd = self.prefix + [
            "shell", "getprop"
        ]
        if not (resp := await Terminal.cmd_line(cmd)):
            return None

        pick: typing.Callable[
            [str], str
        ] = lambda x: m.group(1) if (
            m := re.search(rf"\[{re.escape(x)}]: \[(.*?)]", resp)
        ) else "unknown"

        self.brand    = pick("ro.product.brand")
        self.model    = pick("ro.product.model")
        self.version  = pick("ro.build.version.release")
        self.hardware = pick("ro.hardware")
        self.sdk      = pick("ro.build.version.sdk")
        self.abi      = pick("ro.product.cpu.abi")

        self.locale   = pick("persist.sys.locale")
        self.timezone = pick("persist.sys.timezone")

        self.debuggable = pick("ro.debuggable") == "1"
        self.secure     = pick("ro.secure") == "1"

    # workflow: ==== Info ====
    async def st_battery(self) -> int | None:
        """读取电池电量百分比（level/scale）。"""
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

    # workflow: ==== Info ====
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

    # workflow: ==== Info ====
    async def is_online(self) -> bool:
        """是否能真正访问互联网。"""
        resp = await Terminal.cmd_line(
            self.prefix + ["shell", "ping", "-c", "1", "1.1.1.1"]
        )
        return bool(resp and "1 packets transmitted" in resp)

    # workflow: ==== Info ====
    async def is_emulator(self) -> bool:
        """根据硬件/机型特征判断是否为模拟器。"""
        return (
            "goldfish" in self.hardware or "ranchu" in self.hardware or "sdk" in self.model
        )

    # workflow: ==== Info ====
    async def is_screen_lock(self) -> bool:
        """检查是否正在显示锁屏。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "policy", "|", "grep", "mInputRestricted"
        ]
        return "true" in await Terminal.cmd_line(cmd)

    # workflow: ==== Info ====
    async def is_screen_on(self) -> bool:
        """检查屏幕是否处于点亮状态。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "deviceidle", "|", "grep", "mScreenOn"
        ]
        return "true" in await Terminal.cmd_line(cmd)

    # workflow: ==== Info ====
    async def screenshot(self, local: str) -> str:
        """在设备上截屏 -> pull 到指定本地路径，返回本地路径。"""
        filename = f"screenshot_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        remote = "/data/local/tmp/" + filename

        capture = self.prefix + ["shell", "screencap", "-p", remote]
        await Terminal.cmd_line(capture)

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

        return str(destination)

    # workflow: ==== File ====
    async def file_logcat_link(self) -> asyncio.subprocess.Process:
        """读取日志。"""
        cmd = self.prefix + [
            "logcat", "-v", "threadtime"
        ]
        return await Terminal.cmd_link(cmd)

    # workflow: ==== Keyevent ====
    async def key_event(self, keycode: int, longpress: bool = False) -> typing.Any:
        """向设备发送 Android 系统按键事件（支持普通按键与长按）。"""
        cmd = self.prefix + [
            "shell", "input", "keyevent"
        ]
        if longpress: cmd += ["--longpress"]
        cmd += [str(keycode)]

        return await Terminal.cmd_line(cmd)

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

    # workflow: ==== UI ====
    async def tap(self, x: int, y: int) -> typing.Any:
        """点击指定坐标。"""
        cmd = self.prefix + [
            "shell", "input", "tap", str(x), str(y)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI ====
    async def focus(self) -> tuple[typing.Optional[str], typing.Optional[str], str]:
        """获取当前前台焦点信息（package / activity / raw）。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"
        ]

        if not (resp := await Terminal.cmd_line(cmd)):
            return None, None, ""

        raw: str = str(resp).strip()

        package: typing.Optional[str]  = None
        activity: typing.Optional[str] = None

        # 1) 优先：package/activity（component）
        if m := re.search(r"([a-zA-Z0-9._]+/[a-zA-Z0-9._$]+)", raw):
            activity = m.group(1)
            package = activity.split("/", 1)[0]
        else:
            # 2) 退化：u0 com.xxx.app ...
            if m := re.search(r"\bu\d+\s+([a-zA-Z0-9._]+)\b", raw):
                package = m.group(1)

        return package, activity, raw

    # workflow: ==== UI ====
    async def current_xml(self) -> typing.Optional[str]:
        """导出当前 UI 层级 XML。"""
        xml_file = "/data/local/tmp/window_dump.xml"

        cmd = self.prefix + [
            "shell", "uiautomator", "dump", "--compressed", xml_file
        ]
        await Terminal.cmd_line(cmd)

        cat = self.prefix + ["shell", "cat", xml_file]
        remove = self.prefix + ["shell", "rm", "-f", xml_file]
        try:
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

    # workflow: ==== UI ====
    async def scroll_direction(
        self,
        direction: typing.Literal["up", "down", "left", "right"],
        x: int,
        y: int,
        duration: int = 300
    ) -> typing.Any:
        """
        以锚点为参考，按“内容滚动方向（scroll）”进行语义滑动。
        注意：底层 adb `input swipe` 是“手指轨迹”，因此这里会做方向反转：
          - scroll up    -> finger down
          - scroll down  -> finger up
          - scroll left  -> finger right
          - scroll right -> finger left
        """
        w, h = await self.st_wm_size()

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

    # workflow: ==== UI ====
    async def find_widget(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> typing.Optional[Widget]:
        """Dump 当前页面 XML -> 构建 Widget 列表 -> 按 by/value + match/ignore_case 返回第一个命中控件。"""
        if by == "xpath":
            return None

        if not (xml := await self.current_xml()):
            return None

        xml = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", xml)

        try:
            root = Et.fromstring(xml)
        except (Et.ParseError, TypeError):
            return None

        widget_list = [
            Widget(node.attrib) for node in root.iter("node")
        ]

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


if __name__ == '__main__':
    pass
