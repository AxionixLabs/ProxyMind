# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import re
import typing
import asyncio
import contextlib
import xml.etree.ElementTree as Et
from backend.mcp_hub.hub_device.widget import Widget
from backend.utilities import const
from backend.utilities.process import Flux


class Phone(object):
    """设备原子能力层。"""

    def __init__(self, serial: str):
        self.serial = serial

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
            "secure"     : cls._pick_prop(resp, "ro.secure") == "1"
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

    @staticmethod
    def _sh_quote_single(text: str) -> str:
        """按单引号规则转义 shell 文本。"""
        return "'" + text.replace("'", r"'\''") + "'"

    @staticmethod
    def _activity_component(package: str, activity: str) -> str:
        """生成 Activity component。"""
        activity = str(activity or "").strip()
        if "/" in activity:
            return activity
        return f"{package}/{activity}"

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

    @classmethod
    def parse_widgets(cls, xml: str) -> list[Widget]:
        """解析页面控件列表。"""
        try:
            root = Et.fromstring(cls._normalize_xml(xml))
        except (Et.ParseError, TypeError):
            return []

        return [
            Widget(node.attrib) for node in root.iter("node")
        ]

    @staticmethod
    def parse_package_list(text: str) -> list[str]:
        """解析 `pm list packages` 输出。"""
        pkg_list: list[str] = []
        for line in (text or "").splitlines():
            if not (line := line.strip()):
                continue
            if line.startswith("package:"):
                pkg_list.append(line.split("package:", 1)[1].strip())
            else:
                pkg_list.append(line)

        seen: set[str] = set()
        out: list[str] = []
        for pkg in pkg_list:
            if pkg and pkg not in seen:
                seen.add(pkg)
                out.append(pkg)
        return out

    async def refresh_device_props(self) -> dict[str, typing.Any]:
        """刷新设备属性缓存。"""
        cmd = self.prefix + [
            "shell", "getprop"
        ]
        if not (resp := await Flux.cmd_line(cmd)):
            return self._sync_device_props_cache({})

        return self._sync_device_props_cache(self._parse_device_props(resp, self.serial))

    async def battery(self) -> int | None:
        """读取电池电量百分比。"""
        cmd = self.prefix + ["shell", "dumpsys", "battery"]
        if not (resp := await Flux.cmd_line(cmd)):
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
        if not (resp := await Flux.cmd_line(cmd)):
            return None

        if not (m := re.search(r"Physical size:\s*(\d+)x(\d+)", resp)):
            return None

        return int(m.group(1)), int(m.group(2))

    async def is_online(self) -> bool:
        """检查设备是否联网。"""
        resp = await Flux.cmd_line(
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
        resp = await Flux.cmd_line(cmd)
        return bool(resp and "true" in resp)

    async def is_screen_on(self) -> bool:
        """检查屏幕是否点亮。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "deviceidle", "|", "grep", "mScreenOn"
        ]
        resp = await Flux.cmd_line(cmd)
        return bool(resp and "true" in resp)

    async def screencap(self, remote: str) -> str | None:
        """执行设备端截图。"""
        capture = self.prefix + ["shell", "screencap", "-p", remote]
        return await Flux.cmd_line(capture)

    async def send_keyevent(self, keycode: int, longpress: bool = False) -> str | None:
        """发送系统按键事件。"""
        cmd = self.prefix + [
            "shell", "input", "keyevent"
        ]
        if longpress: cmd += ["--longpress"]
        cmd += [str(keycode)]

        return await Flux.cmd_line(cmd)

    async def tap(self, x: int, y: int) -> str | None:
        """点击指定坐标。"""
        cmd = self.prefix + [
            "shell", "input", "tap", str(x), str(y)
        ]
        return await Flux.cmd_line(cmd)

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> str | None:
        """执行一次滑动手势。"""
        cmd = self.prefix + [
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        ]
        return await Flux.cmd_line(cmd)

    async def app_start(self, package: str, activity: typing.Optional[str] = None) -> str | None:
        """执行应用启动命令。"""
        if activity:
            cmd = self.prefix + [
                "shell", "am", "start", "-n", self._activity_component(package, activity)
            ]
            return await Flux.cmd_line(cmd)

        category = "android.intent.category.LAUNCHER"

        cmd = self.prefix + [
            "shell", "monkey", "-p", package, "-c", category, "1"
        ]
        return await Flux.cmd_line(cmd)

    async def app_stop(self, package: str) -> str | None:
        """执行应用停止命令。"""
        cmd = self.prefix + [
            "shell", "am", "force-stop", package
        ]
        return await Flux.cmd_line(cmd)

    async def shell_script(self, script: str) -> str | None:
        """执行一段设备侧 shell 脚本。"""
        cmd = self.prefix + [
            "shell", "sh", "-c", str(script or "")
        ]
        return await Flux.cmd_line(cmd)

    async def app_deep_link(self, url: str) -> str | None:
        """执行深度链接启动命令。"""
        cmd = self.prefix + [
            "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url
        ]
        return await Flux.cmd_line_shell(" ".join(cmd))

    async def app_clear(self, package: str) -> str | None:
        """执行应用数据清理命令。"""
        cmd = self.prefix + [
            "shell", "pm", "clear", package
        ]
        return await Flux.cmd_line(cmd)

    async def file_pull(self, remote: str, local: str) -> str | None:
        """从设备拉取文件。"""
        cmd = self.prefix + [
            "pull", remote, local
        ]
        return await Flux.cmd_line(cmd)

    async def file_remove(self, path: str) -> str | None:
        """删除设备文件。"""
        cmd = self.prefix + [
            "shell", "rm", "-f", path
        ]
        return await Flux.cmd_line(cmd)

    async def logcat_link(self) -> asyncio.subprocess.Process:
        """连接 logcat 输出流。"""
        cmd = self.prefix + [
            "logcat", "-v", "threadtime"
        ]
        return await Flux.cmd_link(cmd)

    async def logcat_dump(self, tags: typing.Optional[list[str]] = None, level: str = "W") -> str:
        """读取一次性 logcat 输出。"""
        lv = str(level or "W").upper().strip()
        if lv not in {"V", "D", "I", "W", "E", "F", "S"}:
            lv = "W"

        cmd = self.prefix + ["logcat", "-v", "threadtime"]
        cleaned_tags = [str(tag).strip() for tag in (tags or []) if str(tag).strip()]
        if cleaned_tags:
            for tag in cleaned_tags:
                cmd.append(f"{tag}:{lv}")
            cmd.append("*:S")
        else:
            cmd.append(f"*:{lv}")
        cmd.append("-d")
        return await Flux.cmd_line(cmd)

    async def logcat_clean(self) -> str | None:
        """清空 logcat。"""
        cmd = self.prefix + [
            "logcat", "-c"
        ]
        return await Flux.cmd_line(cmd)

    async def list_packages(self, scope: typing.Literal["user", "system", "all"] = "user") -> str:
        """按范围列出包名。"""
        cmd = self.prefix + [
            "shell", "pm", "list", "packages"
        ]
        match scope:
            case "user":
                cmd += ["-3"]
            case "system":
                cmd += ["-s"]
        return await Flux.cmd_line(cmd)

    async def grep_packages(self, keyword: str, scope: typing.Literal["user", "system", "all"] = "user") -> str:
        """按关键字过滤包名。"""
        cmd = self.prefix + [
            "shell", "pm", "list", "packages"
        ]
        match scope:
            case "user":
                cmd += ["-3"]
            case "system":
                cmd += ["-s"]
        cmd += ["|", "grep", "-i", keyword]
        return await Flux.cmd_line(cmd)

    async def open_notification(self) -> str | None:
        """打开通知栏。"""
        cmd = self.prefix + [
            "shell", "cmd", "statusbar", "expand-notifications"
        ]
        return await Flux.cmd_line(cmd)

    async def open_quick_settings(self) -> str | None:
        """打开快捷设置。"""
        cmd = self.prefix + [
            "shell", "cmd", "statusbar", "expand-settings"
        ]
        return await Flux.cmd_line(cmd)

    async def ime_current(self) -> str:
        """读取当前默认输入法。"""
        cmd = self.prefix + [
            "shell", "settings", "get", "secure", "default_input_method"
        ]
        resp = await Flux.cmd_line(cmd)
        return ("" if resp is None else str(resp)).strip()

    async def ime_enable(self, ime: str) -> str:
        """启用输入法。"""
        cmd = self.prefix + [
            "shell", "ime", "enable", ime
        ]
        resp = await Flux.cmd_line(cmd)
        return "" if resp is None else str(resp)

    async def ime_set(self, ime: str) -> str:
        """切换输入法。"""
        cmd = self.prefix + [
            "shell", "ime", "set", ime
        ]
        resp = await Flux.cmd_line(cmd)
        return "" if resp is None else str(resp)

    async def reboot(self, mode: typing.Literal["", "recovery", "bootloader", "edl"] = "") -> str | None:
        """执行重启命令。"""
        cmd = self.prefix + ["reboot"] + ([mode] if mode else [])
        return await Flux.cmd_line(cmd)

    async def wait_for_device(self) -> str | None:
        """等待设备重新上线。"""
        cmd = self.prefix + [
            "wait-for-device"
        ]
        return await Flux.cmd_line(cmd)

    async def input_text(self, text: str) -> str | None:
        """通过 ADB_INPUT_TEXT 广播输入文本。"""
        cmd = self.prefix + [
            "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", self._sh_quote_single(text)
        ]
        return await Flux.cmd_line(cmd)

    async def clear_text(self) -> str | None:
        """通过 ADB_CLEAR_TEXT 广播清空文本。"""
        cmd = self.prefix + [
            "shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"
        ]
        return await Flux.cmd_line(cmd)

    async def focus_info(self) -> dict[str, typing.Optional[str] | str]:
        """获取当前前台焦点。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"
        ]

        if not (resp := await Flux.cmd_line(cmd)):
            return {"package": None, "activity": None, "raw": ""}

        return self._parse_focus(str(resp))

    async def ui_xml(self) -> typing.Optional[str]:
        """导出当前页面 XML。"""
        xml_file = "/data/local/tmp/window_dump.xml"

        cmd = self.prefix + [
            "shell", "uiautomator", "dump", "--compressed", xml_file
        ]
        await Flux.cmd_line(cmd)

        cat = self.prefix + ["shell", "cat", xml_file]
        try:
            # uiautomator dump 生成文件有延迟，短轮询几次比一次性读取更稳。
            for _ in range(6):
                xml = await Flux.cmd_line(cat)

                xml = xml.decode(const.CHARSET, const.IGNORE) if isinstance(
                    xml, (bytes, bytearray)
                ) else (xml or "")

                if "<hierarchy" in xml:
                    return xml
                await asyncio.sleep(0.12)
            return None
        finally:
            with contextlib.suppress(Exception):
                await self.file_remove(xml_file)

    async def ui_widgets(self) -> list[Widget]:
        """获取当前页面控件列表。"""
        if not (xml := await self.ui_xml()):
            return []

        return self.parse_widgets(xml)

    async def scroll_by_direction(
        self,
        direction: typing.Literal["up", "down", "left", "right"],
        x: int,
        y: int,
        duration: int = 300
    ) -> str | None:
        """按内容方向执行语义滑动。"""
        if not (wm := await self.wm_size()):
            return None

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

        return await self.swipe(x1, y1, x2, y2, duration)

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
