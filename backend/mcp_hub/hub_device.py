#  _   _       _       ____             _
# | | | |_   _| |__   |  _ \  _____   _(_) ___ ___
# | |_| | | | | '_ \  | | | |/ _ \ \ / / |/ __/ _ \
# |  _  | |_| | |_) | | |_| |  __/\ V /| | (_|  __/
# |_| |_|\__,_|_.__/  |____/ \___| \_/ |_|\___\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import uuid
import base64
import typing
import asyncio
import secrets
import datetime
import tempfile
import contextlib
import numpy as np
from PIL import Image
from pathlib import Path
import xml.etree.ElementTree as Et
from engine.terminal import Terminal
from backend.utilities import const


class Device(object):
    """Device class."""

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

        online = device_snap.get("online") is True
        locked = device_snap.get("screen_lock") is True

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
            ("secure",     brief(device_snap.get("secure") is True)),
            ("debuggable", brief(device_snap.get("debuggable") is True)),
            ("emulator",   brief(device_snap.get("emulator") is True)),
        ])

        semantic_brief = (
            f"{device_snap.get('serial') or 'unknown'}: "
            f"{'在线' if online else '离线'} / "
            f"{'锁屏' if locked else '未锁屏'} / "
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

    # workflow: ==== Device Info MCP Tool ====
    async def device_snapshot(self) -> dict:
        """采集并返回该设备当前所有状态快照。"""
        battery     = await self.st_battery()
        wm_size     = await self.st_wm_size()
        online      = await self.is_online()
        emulator    = await self.is_emulator()
        screen_lock = await self.is_screen_lock()

        information = self.device_info | {
            "battery"     : battery,
            "wm_size"     : {"w": wm_size[0], "h": wm_size[1]} if wm_size else None,
            "online"      : online,
            "emulator"    : emulator,
            "screen_lock" : screen_lock
        }

        return self.device_semantics(information)["semantic_brief"]

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
        self.secure     = pick("ro.secure") == "1"

    # workflow: ==== Device ====
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
            self.prefix + ["shell", "ping", "-c", "1", "1.1.1.1"]
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
            "shell", "dumpsys", "window", "policy", "|", "grep", "mInputRestricted"
        ]
        return "true" in await Terminal.cmd_line(cmd)

    # workflow: ==== Device ====
    async def is_screen_on(self) -> bool:
        """检查屏幕是否处于点亮状态。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "deviceidle", "|", "grep", "mScreenOn"
        ]
        return "true" in await Terminal.cmd_line(cmd)

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
    async def app_install(
        self,
        apk: str,
        replace: bool = True,
        downgrade: bool = False,
        test: bool = False
    ) -> typing.Any:
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
    async def pull(self, remote: str, local: str) -> str:
        """从设备拉取文件到本地。"""
        unique = secrets.token_hex(6)

        if (p := Path(local)).suffix:
            destination = p.with_name(f"{p.stem}_{self.serial}_{unique}{p.suffix}")
        else:
            destination = p / f"pull_{self.serial}_{unique}.bin"

        cmd = self.prefix + [
            "pull", remote, destination
        ]
        await Terminal.cmd_line(cmd)

        return str(destination)

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

    # workflow: ==== File Control MCP Tool ====
    async def logcat_dump(
        self,
        since_sec: int = 5,
        keywords: typing.Optional[list[str]] = None,
        max_lines: int = 200,
        saved: typing.Optional[str] = None
    ) -> dict:
        """一次性拉取 logcat 快照；按 keywords(不分大小写 OR) 过滤；saved=None 返回尾部 max_lines；saved=目录/文件则保存全量(不受200行限制)。"""
        await self.logcat_clean()

        cmd = self.prefix + ["logcat", "-v", "threadtime", "-d"]

        # since_sec -> -T
        try:
            ss = int(since_sec)
        except (TypeError, ValueError):
            ss = 5
        ss = 5 if ss <= 0 else ss

        dt = datetime.datetime.now() - datetime.timedelta(seconds=ss)
        ts = dt.strftime("%m-%d %H:%M:%S.000")
        cmd += ["-T", ts]

        raw = await Terminal.cmd_line(cmd)
        text = raw or ""
        lines = text.splitlines()

        # keywords 过滤：OR + 不分大小写
        ks: list[str] = []
        if keywords:
            ks = [str(k).strip() for k in keywords if k and str(k).strip()]
        if ks:
            pattern = re.compile("|".join(re.escape(k) for k in ks), re.IGNORECASE)
            lines = [ln for ln in lines if pattern.search(ln)]

        # saved：目录/文件都支持；目录默认生成文件名；无后缀自动补 .log
        if saved and str(saved).strip():
            p = Path(str(saved)).expanduser()

            if p.suffix:  # 明确文件
                out = p
            else:
                # 认为是目录（无后缀）：确保目录存在，并生成默认文件名
                out_dir = p
                out_dir.mkdir(parents=True, exist_ok=True)
                name = f"logcat_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.log"
                out = out_dir / name

            if not out.suffix:
                out = out.with_suffix(".log")

            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(lines), const.CHARSET, const.IGNORE)
            saved_path = str(out.resolve())

            return {
                "text": f"logcat saved: {Path(saved_path).name}",
                "attachments": [
                    {"kind": "file", "path": saved_path, "filename": Path(saved_path).name, "mime_type": "text/plain"}
                ],
                "data": {
                    "lines"     : len(lines),
                    "truncated" : False,
                    "since_sec" : ss,
                    "keywords"  : ks,
                    "saved"     : saved_path
                },
                "logs": []
            }

        # 未保存：尾部截断
        truncated = False
        if 0 < (ml := int(max_lines) if max_lines else 0) < len(lines):
            truncated, lines = True, lines[-ml:]

        return {
            "text": "\n".join(lines),
            "attachments": [],
            "data": {
                "lines"     : len(lines),
                "truncated" : truncated,
                "since_sec" : ss,
                "keywords"  : ks,
                "saved"     : None
            },
            "logs": []
        }

    # workflow: ==== File Control MCP Tool ====
    async def logcat_clean(self) -> typing.Any:
        """清空日志。"""
        cmd = self.prefix + [
            "logcat", "-c"
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== File ====
    async def logcat_start(self) -> asyncio.subprocess.Process:
        """读取日志。"""
        cmd = self.prefix + [
            "logcat", "-v", "threadtime"
        ]
        return await Terminal.cmd_link(cmd)

    # workflow: ==== Media Control MCP Tool ====
    async def screenshot(self, local: str) -> str:
        """在设备上截屏 -> pull 到指定本地路径，返回本地路径。"""
        filename = f"screenshot_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.png"

        cmd = self.prefix + [
            "shell", "screencap", "-p", remote := f"/data/local/tmp/{filename}"
        ]
        await Terminal.cmd_line(cmd)

        new_local = await self.pull(remote, local)
        await self.remove(remote)

        return new_local

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
    async def ime_reset(self) -> typing.Any:
        """还原到系统默认输入法（等同于 `adb shell ime reset`）。"""
        cmd = self.prefix + ["shell", "ime", "reset"]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System Control MCP Tool ====
    async def device_reboot(
        self,
        mode: typing.Literal["", "recovery", "bootloader", "edl"] = "",
        wait: bool = False,
        wait_timeout: float = 120.0
    ) -> dict[str, typing.Any]:
        """
        重启设备（adb reboot）。

        Args:
            mode:
                ""           -> 普通重启（adb reboot）
                "recovery"   -> 重启到 recovery（adb reboot recovery）
                "bootloader" -> 重启到 bootloader/fastboot（adb reboot bootloader）
                "edl"        -> 重启到 EDL（部分设备支持）（adb reboot edl）
            wait:
                是否等待设备重新上线（adb wait-for-device）。
                注意：bootloader/edl 场景一般不会回到 adb online，此时不要 wait=True。
            wait_timeout:
                等待设备上线的超时时间（秒）。
        """
        cmd = self.prefix + ["reboot"] + ([mode] if mode else [])

        raw = await Terminal.cmd_line(cmd)
        out = ("" if raw is None else str(raw)).strip()

        data: dict[str, typing.Any] = {"ok": True, "mode": mode, "cmd": cmd, "out": out}

        # 仅普通重启才支持 wait-for-device
        if wait and mode == "":
            try:
                await asyncio.wait_for(
                    Terminal.cmd_line(self.prefix + ["wait-for-device"]),
                    timeout=wait_timeout
                )
                return {
                    "text"        : "设备已触发重启，并已重新上线。",
                    "attachments" : [],
                    "data"        : data
                }
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                data["ok"] = False
                data["error"] = err
                data["wait_timeout"] = wait_timeout
                return {
                    "text"        : f"设备已触发重启，但等待重新上线失败：{err}",
                    "attachments" : [],
                    "data"        : data
                }

        # 不等待：只表示命令已下发
        return {
            "text"        : "已下发 adb reboot 指令。",
            "attachments" : [],
            "data"        : data
        }

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
          - scroll up   -> finger down
          - scroll down -> finger up
          - scroll left -> finger right
          - scroll right-> finger left
        """

        w, h = await self.st_wm_size()

        x1, y1 = x, y
        x2, y2 = x1, y1

        # 这里的 0.25 / 0.75 只是目标落点比例：你也可以改成基于锚点的偏移量
        match direction:
            # 内容向上滚：手指向下
            case "up":
                x2, y2 = x1, min(h - 1, int(h * 0.75))

            # 内容向下滚：手指向上
            case "down":
                x2, y2 = x1, max(0, int(h * 0.25))

            # 内容向左滚：手指向右
            case "left":
                x2, y2 = min(w - 1, int(w * 0.75)), y1

            # 内容向右滚：手指向左
            case "right":
                x2, y2 = max(0, int(w * 0.25)), y1

        cmd = self.prefix + [
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_to_top(self) -> dict[str, typing.Any]:
        """内容向上滚动到顶部。"""
        return await self.scroll_to_edge("top")

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_to_bottom(self) -> dict[str, typing.Any]:
        """内容向下滚动到底部。"""
        return await self.scroll_to_edge("bottom")

    # workflow: ==== UI Interaction MCP Tool ====
    async def tap(self, x: int, y: int) -> typing.Any:
        """点击指定坐标。"""
        cmd = self.prefix + [
            "shell", "input", "tap", str(x), str(y)
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def click(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list
    ) -> typing.Any:
        """根据选择器点击对应节点中心点。"""

        if not (node := await self.find_node(by, value)):
            return None

        return await self.tap(*node["center"])

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
        def sh_quote_single(s: str) -> str:
            """Quote a string for POSIX shell using single quotes."""
            return "'" + s.replace("'", r"'\''") + "'"

        if not (ime := await self.ensure_ime()).get("data", {}).get("ok"):
            return ime

        text = "" if text is None else str(text)
        text = sh_quote_single(text)

        cmd = self.prefix + [
            "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def clear_text(self) -> typing.Any:
        """通过 AdbIME 清空当前焦点输入框文本（等同于 `adb shell am broadcast -a ADB_CLEAR_TEXT`）。"""
        if not (ime := await self.ensure_ime()).get("data", {}).get("ok"):
            return ime

        cmd = self.prefix + [
            "shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_focus(self) -> dict[str, typing.Any]:
        """获取当前前台焦点信息（package / activity / raw）。"""
        cmd = self.prefix + [
            "shell", "dumpsys", "window", "|", "grep", "mCurrentFocus"
        ]

        if not (resp := await Terminal.cmd_line(cmd)):
            return {
                "text": "获取当前 Focus 失败：dumpsys/grep 无输出",
                "attachments": [],
                "data": {
                    "ok"       : False,
                    "stage"    : "dumpsys_window",
                    "reason"   : "empty_output",
                    "package"  : None,
                    "activity" : None,
                    "raw"      : None,
                    "cmd"      : cmd
                },
                "log": []
            }

        raw = str(resp).strip()

        package: str | None = None
        activity: str | None = None

        # 1) 优先：package/activity（component）
        if m := re.search(r"([a-zA-Z0-9._]+/[a-zA-Z0-9._$]+)", raw):
            activity = m.group(1)
            package = activity.split("/", 1)[0]
        else:
            # 2) 退化：u0 com.xxx.app ...
            if m := re.search(r"\bu\d+\s+([a-zA-Z0-9._]+)\b", raw):
                package = m.group(1)

        ok = bool(package or activity)

        return {
            "text": f"当前Focus：package={package or ''} activity={activity or ''}".strip(),
            "attachments": [],
            "data": {
                "ok"       : ok,
                "stage"    : "parse",
                "reason"   : None if ok else "parse_failed",
                "package"  : package,
                "activity" : activity,
                "raw"      : raw,
                "cmd"      : cmd
            },
            "log": []
        }

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_xml(self) -> str | None:
        """导出当前 UI 层级 XML。"""
        xml_file = "/data/local/tmp/window_dump.xml"

        cmd = self.prefix + ["shell", "uiautomator", "dump", "--compressed", xml_file]
        await Terminal.cmd_line(cmd)

        cat = self.prefix + ["shell", "cat", xml_file]
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
                await self.remove(xml_file)

    # workflow: ==== UI Interaction MCP Tool ====
    async def find_element(self, locator: str, *_, **__) -> dict:
        """执行自愈流程定位并处理目标控件。"""
        page_id, page_dump, (w, h) = await asyncio.gather(
            self.current_focus(), self.current_xml(), self.st_wm_size()
        )
        payload = {
            "serial"    : self.serial,
            "page_id"   : page_id.get("data", {}).get("package") or "",
            "platform"  : "android",
            "locator"   : locator,
            "page_dump" : page_dump or "",
            "wm_size"   : {"w": w, "h": h}
        }

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            new_local = await self.screenshot(tmp.name)
            with open(new_local, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            payload["screenshot_base64"] = b64
            payload["screenshot_data_url"] = f"data:image/png;base64,{b64}"

        os.remove(new_local)

        return payload

    # workflow: ==== UI ====
    async def wait_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        mode: typing.Literal["exists", "gone"] = "exists",
        timeout: float = 10.0
    ) -> bool:
        """等待节点出现/消失；mode='exists' 等出现，mode='gone' 等消失。"""

        want_exists = (mode == "exists")

        deadline = time.monotonic() + timeout

        while True:
            if bool(await self.find_node(by, value)) == want_exists:
                return True

            if time.monotonic() >= deadline:
                return False

            await asyncio.sleep(0.25)

    # workflow: ==== UI ====
    async def find_node(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list
    ) -> typing.Optional[dict]:
        """统一查找节点：返回 node/bounds/center（用于 click / wait / heal）"""

        if by == "bbox":
            # bbox 直接计算中心点，无需解析 XML
            x1, y1, x2, y2 = value
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            return {"node": None, "bounds": [x1, y1, x2, y2], "center": [cx, cy]}

        if by == "xpath": return None  # Android dump 非标准 XPath，暂不支持

        if not (xml := await self.current_xml()):
            return None

        mapped_by = self.map_by(by)  # 将 id/desc 映射到 Android 属性名

        for n in Et.fromstring(xml).iter("node"):
            if n.attrib.get(mapped_by) == value:
                bounds_str = n.attrib.get("bounds", "")
                if not (bounds := self.parse_bounds(bounds_str)):
                    return {"node": n.attrib, "bounds": None, "center": None}

                x1, y1, x2, y2 = bounds  # 解析 bounds 为四点坐标
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2  # 计算中心点

                return {"node": n.attrib, "bounds": [x1, y1, x2, y2], "center": [cx, cy]}

        return None

    # workflow: ==== UI ====
    async def ensure_ime(self) -> dict[str, typing.Any]:
        """切换到 AdbIME；若 enable/set 任一提示 Unknown input method，则直接返回错误结果。"""
        ime = "com.android.adbkeyboard/.AdbIME"
        cmd = self.prefix + ["shell", "settings", "get", "secure", "default_input_method"]
        if (await Terminal.cmd_line(cmd) or "").strip() == ime:
            return {
                "text"        : "当前已是 AdbIME 输入法",
                "attachments" : [],
                "data"        : {"ok": True}
            }

        e_out = await Terminal.cmd_line(self.prefix + ["shell", "ime", "enable", ime])
        s_out = await Terminal.cmd_line(self.prefix + ["shell", "ime", "set", ime])

        e_text = "" if e_out is None else str(e_out)
        s_text = "" if s_out is None else str(s_out)

        e_low, s_low = e_text.lower(), s_text.lower()

        merged = f"{e_text}\n{s_text}".lower()

        if "unknown" in merged or "cannot" in merged:
            stage = []
            if "unknown" in e_low or "cannot" in e_low:
                stage.append("enable")
            if "unknown" in s_low or "cannot" in s_low:
                stage.append("set")

            return {
                "text"        : "无法切换到 AdbIME：设备未安装或未注册该输入法",
                "attachments" : [],
                "data": {
                    "ok"         : False,
                    "stage"      : stage,  # ["enable"] / ["set"] / ["enable","set"]
                    "ime_target" : ime,
                    "reason"     : "ime_not_found",
                    "enable_raw" : e_text,
                    "set_raw"    : s_text,
                    "suggest"    : ["先安装 ADBKeyBoard（https://github.com/senzhk/ADBKeyBoard）"]
                }
            }

        current = (await Terminal.cmd_line(cmd)) or ""

        if current.strip() == ime:
            return {
                "text"        : "已切换到 AdbIME",
                "attachments" : [],
                "data"        : {"ok": True}
            }

        return {
            "text"        : "已执行切换命令，但未检测到输入法切换成功",
            "attachments" : [],
            "data": {
                "ok"         : False,
                "current"    : current,
                "reason"     : "ime_not_effective",
                "enable_raw" : e_text,
                "set_raw"    : s_text
            }
        }

    # workflow: ==== UI ====
    async def scroll_to_edge(self, edge: typing.Literal["top", "bottom"] = "top") -> dict[str, typing.Any]:
        """滑动到边界：top=回到顶部(手指上->下)，bottom=滑到底部(手指下->上)"""
        max_swipes: int = 30

        duration_ms: int = 450
        settle_ms: int   = 450

        similarity_threshold: float = 0.992  # 相似度阈值
        stable_required: int        = 3      # 连续 3 次相似才停
        min_swipes_before_stop: int = 2      # 至少滑 2 次后才允许停

        x_ratio: float     = 0.5
        upper_ratio: float = 0.20  # 更靠近边缘一点 => 滑动更明显
        lower_ratio: float = 0.80

        w, h = await self.st_wm_size()
        x = int(w * x_ratio)

        y_upper = int(h * upper_ratio)
        y_lower = int(h * lower_ratio)

        if edge == "top":
            y_from, y_to = y_upper, y_lower  # 👇 swipe down (回到更上面)
            gesture = "down"
        else:
            y_from, y_to = y_lower, y_upper  # 👆 swipe up (去到更下面)
            gesture = "up"

        last_sim    = 0.0
        stable_hits = 0

        with tempfile.TemporaryDirectory(prefix="scroll_caps_") as tmp:
            tmp_dir   = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path  = str(tmp_dir / "cur.png")

            prev = await self.screenshot(prev_path)

            for n in range(1, max_swipes + 1):
                await self.swipe(x, y_from, x, y_to, duration_ms)
                await asyncio.sleep(settle_ms / 1000)

                cur = await self.screenshot(cur_path)
                last_sim = float(self.image_similarity(prev, cur))

                # 防抖：累计连续“几乎不变”的次数
                if last_sim >= similarity_threshold:
                    stable_hits += 1
                else:
                    stable_hits = 0

                # 至少滑动几次后，且连续 stable_required 次相似才停
                if n >= min_swipes_before_stop and stable_hits >= stable_required:
                    return {
                        "ok"          : True,
                        "edge"        : edge,
                        "gesture"     : gesture,
                        "swipes"      : n,
                        "similarity"  : round(last_sim, 4),
                        "stable_hits" : stable_hits,
                        "reason"      : "screen_not_changed"
                    }

                prev, cur = cur, prev
                prev_path, cur_path = cur_path, prev_path

            return {
                "ok"          : True,
                "edge"        : edge,
                "gesture"     : gesture,
                "swipes"      : max_swipes,
                "similarity"  : round(last_sim, 4),
                "stable_hits" : stable_hits,
                "reason"      : "max_swipes_reached"
            }

    # workflow: ==== UI ====
    @staticmethod
    def map_by(by: str) -> str:
        """统一选择器字段到 Android XML 属性名。"""
        match by:
            case "id"   : return "resource-id"
            case "desc" : return "content-desc"

        return by

    # workflow: ==== UI ====
    @staticmethod
    def parse_bounds(bounds: str) -> typing.Optional[tuple[int, int, int, int]]:
        """解析 Android bounds 字符串："[x1,y1][x2,y2]" -> (x1,y1,x2,y2)。"""
        pattern = re.compile(r"\[(\d+),(\d+)]\[(\d+),(\d+)]")

        if not (m := pattern.match(bounds)):
            return None

        x1, y1, x2, y2 = map(int, m.groups())

        return x1, y1, x2, y2

    # workflow: ==== UI ====
    @staticmethod
    def image_similarity(img_path1: str, img_path2: str) -> float:
        """0~1：越大越相似（基于灰度 + downscale + 加权MSE，含轻微裁剪增强）。"""
        size: tuple[int, int] = (96, 96)

        # 裁剪比例：去掉顶部/底部固定栏（默认较温和，可按实际 UI 调）
        crop_top: float    = 0.15
        crop_bottom: float = 0.12
        crop_left: float   = 0.00
        crop_right: float  = 0.00

        # 中心权重：越大越强调中心（1.0=无权重）
        center_weight: float = 1.8

        def prepare(path: str) -> np.ndarray:
            with Image.open(path) as im:
                im = im.convert("L")
                width, height = im.size

                # 裁剪边缘，减少状态栏/导航栏/吸顶影响
                left   = int(width * crop_left)
                right  = int(width * (1.0 - crop_right))
                top    = int(height * crop_top)
                bottom = int(height * (1.0 - crop_bottom))

                # 兜底：裁剪不能把图裁没
                if (right - left) >= 4 and (bottom - top) >= 4:
                    im = im.crop((left, top, right, bottom))

                # 再缩放到固定大小
                im = im.resize(size)

                return np.asarray(im, dtype=np.float32)

        a1 = prepare(img_path1)
        a2 = prepare(img_path2)

        # 中心加权（边缘权重低一点，中心权重高一点）
        h, w = a1.shape
        yy, xx = np.mgrid[0:h, 0:w]
        cy, cx = max(1e-6, (h - 1) / 2.0), max(1e-6, (w - 1) / 2.0)

        # 归一化半径：中心0，边缘~1
        r = np.sqrt(((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2)
        r = np.clip(r, 0.0, 1.0)

        # 权重：中心 = center_weight，边缘 = 1.0（平滑过渡）
        weights = 1.0 + (center_weight - 1.0) * (1.0 - r) ** 2

        diff = a1 - a2
        mse = float(np.sum((diff * diff) * weights)) / float(np.sum(weights))

        # 归一化：像素范围 0~255，最大 MSE=255^2
        sim = 1.0 - min(1.0, mse / (255.0 * 255.0))
        return float(sim)


if __name__ == '__main__':
    pass
