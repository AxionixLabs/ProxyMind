#  ____             _
# |  _ \  _____   _(_) ___ ___
# | | | |/ _ \ \ / / |/ __/ _ \
# | |_| |  __/\ V /| | (_|  __/
# |____/ \___| \_/ |_|\___\___|
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import base64
import typing
import asyncio
import secrets
import tempfile
from pathlib import Path
from backend.models.model_device import (
    Attachment,
    SemanticResult,
)
from .combo import Combo
from .widget import Widget
from .vision import similarity
from backend.utilities import const
from engine.terminal import Terminal


class Device(Combo):
    """设备工具语义层。"""

    battery = None
    file_logcat_link = None
    find_ui_widget = None
    focus_info = None
    ime = None
    is_emulator = None
    is_online = None
    is_screen_locked = None
    is_screen_on = None
    refresh_device_props = None
    save_screenshot = None
    screen = None
    screencap = None
    scroll_by_direction = None
    scroll_find = None
    send_keyevent = None
    set_bluetooth = None
    set_mobile_data = None
    set_wifi = None
    tap = None
    ui_widgets = None
    ui_xml = None
    wait_fg = None
    wm_size = None

    def __init__(self, serial: str):
        super().__init__(serial)

    async def _battery(self) -> int | None:
        return await super().battery()

    async def _file_logcat_link(self) -> asyncio.subprocess.Process:
        return await super().file_logcat_link()

    async def _find_ui_widget(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> typing.Optional[Widget]:
        return await super().find_ui_widget(by, value, match, ignore_case)

    async def _focus_info(self) -> dict[str, typing.Optional[str] | str]:
        return await super().focus_info()

    async def _ime(self) -> dict[str, typing.Any]:
        return await super().ime()

    async def _is_emulator(self) -> bool:
        return await super().is_emulator()

    async def _is_online(self) -> bool:
        return await super().is_online()

    async def _is_screen_locked(self) -> bool:
        return await super().is_screen_locked()

    async def _is_screen_on(self) -> bool:
        return await super().is_screen_on()

    async def _refresh_device_props(self) -> dict[str, typing.Any]:
        return await super().refresh_device_props()

    async def _save_screenshot(self, local: str) -> str:
        return await super().save_screenshot(local)

    async def _screen(self, on: bool, settle: float = 0.2) -> None:
        return await super().screen(on, settle)

    async def _screencap(self, remote: str) -> typing.Any:
        return await super().screencap(remote)

    async def _scroll_by_direction(
        self,
        direction: typing.Literal["up", "down", "left", "right"],
        x: int,
        y: int,
        duration: int = 300
    ) -> typing.Any:
        return await super().scroll_by_direction(direction, x, y, duration)

    async def _scroll_find(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        direction: typing.Literal["down", "up", "left", "right"] = "down",
        anchor: typing.Optional[tuple[int, int]] = None,
        duration: int = 320,
        settle: float = 0.25,
        timeout: float = 12.0,
        max_swipes: int = 12,
        stop_on_stable: bool = True,
        similarity_threshold: float = 0.992,
        stable_required: int = 2,
        min_swipes_before_stop: int = 2
    ) -> dict[str, typing.Any]:
        return await super().scroll_find(
            by, value, match, ignore_case, direction, anchor, duration, settle,
            timeout, max_swipes, stop_on_stable, similarity_threshold,
            stable_required, min_swipes_before_stop
        )

    async def _send_keyevent(self, keycode: int, longpress: bool = False) -> typing.Any:
        return await super().send_keyevent(keycode, longpress)

    async def _set_bluetooth(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        return await super().set_bluetooth(status)

    async def _set_mobile_data(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        return await super().set_mobile_data(status)

    async def _set_wifi(self, status: typing.Literal["enable", "disable"]) -> typing.Any:
        return await super().set_wifi(status)

    async def _tap(self, x: int, y: int) -> typing.Any:
        return await super().tap(x, y)

    async def _ui_widgets(self) -> list[Widget]:
        return await super().ui_widgets()

    async def _ui_xml(self) -> typing.Optional[str]:
        return await super().ui_xml()

    async def _wait_fg(
        self,
        package: str,
        wait_s: float,
        poll: float,
        stable_hits: int
    ) -> tuple[bool, dict[str, typing.Any], int]:
        return await super().wait_fg(package, wait_s, poll, stable_hits)

    async def _wm_size(self) -> tuple[int, int] | None:
        return await super().wm_size()

    def __str__(self):
        """返回调试展示文本。"""
        props = self.device_props
        return (
            f"<Device {props.get('brand')} {props.get('model')} "
            f"serial={self.serial} version={props.get('version')} hardware={props.get('hardware')} "
            f"sdk={props.get('sdk')} abi={props.get('abi')} locale={props.get('locale')} "
            f"timezone={props.get('timezone')} debuggable={props.get('debuggable')} secure={props.get('secure')}>"
        )

    __repr__ = __str__

    @staticmethod
    def _device_semantics(device_snap: dict) -> dict:
        """构建设备快照的语义描述。"""
        # Device 层负责把结构化设备信息进一步压成“可读摘要 + 语义键值”。

        def brief(v: typing.Optional[bool]) -> typing.Optional[str]:
            if v is None:
                return None
            return "true" if v else "false"

        def kv(items: list[tuple[str, typing.Any]]) -> str:
            parts: list[str] = []
            for k, v in items:
                if v is None:
                    continue
                if isinstance(v, str) and not v.strip():
                    continue
                parts.append(f"{k}={v}")
            return "; ".join(parts)

        wm = device_snap.get("wm_size") or {}
        screen = f"{wm.get('w')}x{wm.get('h')}" if (wm.get("w") and wm.get("h")) else None

        battery = device_snap.get("battery")
        battery_s = f"{battery}%" if battery is not None else None

        online = device_snap.get("online") is True
        locked = device_snap.get("screen_lock") is True
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
            "kv"    : semantic_kv,
            "brief" : semantic_brief
        }

    @staticmethod
    def _scroll_find_text(reason: typing.Optional[str]) -> str:
        """返回滚动查找结果文案。"""
        if reason == "xpath_not_supported":
            return "by=xpath 暂不支持（Android uiautomator dump 非标准 XPath）。"
        if reason == "wm_size_unavailable":
            return "获取屏幕尺寸失败。"
        if reason == "timeout":
            return "超时未找到目标元素。"
        if reason == "scroll_fail":
            return "滑动失败，已停止。"
        if reason == "stable_stop":
            return "屏幕内容稳定（几乎不变），停止滑动，仍未找到目标元素。"
        if reason == "max_swipes_reached":
            return "已达到最大滑动次数，仍未找到目标元素。"
        return "滚动查找失败。"

    # workflow: ==== Info Control MCP Tool ====
    async def device_snapshot(self) -> dict[str, typing.Any]:
        """返回设备当前快照。"""
        # 这里是 Device 门面的典型做法：汇总底层能力，再统一包装成 SemanticResult。
        props = await self._refresh_device_props()
        battery     = await self._battery()
        wm_size     = await self._wm_size()
        online      = await self._is_online()
        emulator    = await self._is_emulator()
        screen_lock = await self._is_screen_locked()
        screen_on   = await self._is_screen_on()

        information = props | {
            "battery"     : battery,
            "wm_size"     : {"w": wm_size[0], "h": wm_size[1]} if wm_size else None,
            "online"      : online,
            "emulator"    : emulator,
            "screen_lock" : screen_lock,
            "screen_on"   : screen_on
        }
        semantic = self._device_semantics(information)

        return SemanticResult.from_text(
            semantic["brief"],
            data={
                "ok"          : True,
                "reason"      : None,
                "serial"      : information.get("serial"),
                "brand"       : information.get("brand"),
                "model"       : information.get("model"),
                "version"     : information.get("version"),
                "sdk"         : information.get("sdk"),
                "battery"     : information.get("battery"),
                "wm_size"     : information.get("wm_size"),
                "online"      : information.get("online"),
                "emulator"    : information.get("emulator"),
                "screen_lock" : information.get("screen_lock"),
                "screen_on"   : information.get("screen_on")
            }
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_deep_link(self, url: str) -> typing.Any:
        """通过深度链接启动应用。"""
        cmd = self.prefix + [
            "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url
        ]
        await Terminal.cmd_line_shell(" ".join(cmd))
        return SemanticResult.from_text(
            "深度链接已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_start(self, package: str, activity: typing.Optional[str] = None) -> typing.Any:
        """启动指定应用。"""
        action   = "android.intent.action.MAIN"
        category = "android.intent.category.LAUNCHER"

        if activity:
            cmd = self.prefix + [
                "shell", "am", "start", "-a", action, "-c", category, "-n", f"{package}/{activity}"
            ]
            await Terminal.cmd_line(cmd)
            return SemanticResult.from_text(
                "应用启动命令已执行。",
                data={"ok": True, "reason": None}
            ).to_dict()

        cmd = self.prefix + [
            "shell", "monkey", "-p", package, "-c", category, "1"
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "应用启动命令已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_stop(self, package: str) -> typing.Any:
        """强制停止指定应用。"""
        cmd = self.prefix + [
            "shell", "am", "force-stop", package
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "应用停止命令已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_install(
        self,
        apk: str,
        replace: bool = True,
        downgrade: bool = False,
        test: bool = False
    ) -> typing.Any:
        """安装 APK 文件。"""
        cmd = self.prefix + ["install"]

        if replace: cmd.append("-r")
        if downgrade: cmd.append("-d")
        if test: cmd.append("-t")

        cmd.append(apk)

        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "APK 安装命令已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_uninstall(self, package: str, keep_data: bool = False) -> typing.Any:
        """卸载指定应用。"""
        cmd = self.prefix + [
            "shell", "pm", "uninstall"
        ]

        if keep_data: cmd.append("-k")

        cmd.append(package)

        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "应用卸载命令已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_clear(self, package: str) -> typing.Any:
        """清除指定应用的数据。"""
        cmd = self.prefix + [
            "shell", "pm", "clear", package
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "应用数据清理命令已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== App Control MCP Tool ====
    async def app_foreground(
        self,
        package: str,
        activity: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """确保应用位于前台。"""
        t0 = time.time()

        poll: float = 0.25

        quick_wait: float = 0.8
        quick_hits: int   = 1

        first_wait: float = 8.0
        first_hits: int   = 2

        retry_wait: float = 5.0
        retry_hits: int   = 2

        # 快速检查
        ok0, focus0, _ = await self._wait_fg(package, quick_wait, poll, quick_hits)
        if ok0:
            return SemanticResult.from_text(
                "应用已在前台，无需拉起。",
                data={
                    "ok"      : True,
                    "reason"  : None,
                    "stage"   : "already",
                    "focus"   : focus0,
                    "cost_ms" : int((time.time() - t0) * 1000)
                }
            ).to_dict()

        # 首次拉起
        await self.app_start(package, activity)

        ok1, focus1, _ = await self._wait_fg(package, first_wait, poll, first_hits)
        if ok1:
            return SemanticResult.from_text(
                "应用已成功进入前台。",
                data={
                    "ok"      : True,
                    "reason"  : None,
                    "stage"   : "start",
                    "focus"   : focus1,
                    "cost_ms" : int((time.time() - t0) * 1000)
                }
            ).to_dict()

        # 默认重试一次
        await self.app_stop(package)
        await self.app_start(package, activity)

        ok2, focus2, _ = await self._wait_fg(package, retry_wait, poll, retry_hits)
        if ok2:
            return SemanticResult.from_text(
                "首次拉起未命中前台，重试后已进入前台。",
                data={
                    "ok"      : True,
                    "reason"  : None,
                    "stage"   : "retry",
                    "focus"   : focus2,
                    "cost_ms" : int((time.time() - t0) * 1000)
                }
            ).to_dict()

        return SemanticResult.from_text(
            "拉起应用超时（已重试一次仍失败）。",
            data={
                "ok"      : False,
                "reason"  : "retry_timeout",
                "stage"   : "retry",
                "focus"   : focus2,
                "cost_ms" : int((time.time() - t0) * 1000)
            }
        ).to_dict()

    # workflow: ==== File Control MCP Tool ====
    async def file_pull(self, remote: str, local: str) -> dict[str, typing.Any]:
        """从设备拉取文件。"""
        unique = secrets.token_hex(6)

        if (p := Path(local)).suffix:
            destination = p.with_name(f"{p.stem}_{self.serial}_{unique}{p.suffix}")
        else:
            destination = p / f"pull_{self.serial}_{unique}.bin"

        # 转成绝对路径（并规范化）
        destination = destination.expanduser().resolve()
        # 确保父目录存在
        destination.parent.mkdir(parents=True, exist_ok=True)

        cmd = self.prefix + [
            "pull", remote, destination
        ]
        await Terminal.cmd_line(cmd)

        return SemanticResult.from_text(
            f"文件已拉取到 {destination}",
            data={"ok": True, "reason": None, "path": str(destination)}
        ).to_dict()

    # workflow: ==== File Control MCP Tool ====
    async def file_push(self, local: str, remote: str) -> typing.Any:
        """向设备推送文件。"""
        cmd = self.prefix + [
            "push", local, remote
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "文件推送命令已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== File Control MCP Tool ====
    async def file_remove(self, path: str) -> typing.Any:
        """删除设备文件。"""
        cmd = self.prefix + [
            "shell", "rm", "-f", path
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "文件删除命令已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== File Control MCP Tool ====
    async def file_logcat_dump(
        self,
        keywords: typing.Optional[list[str]] = None,
        tags: typing.Optional[list[str]] = None,
        level: str = "W",
        max_lines: int = 200,
        saved: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """导出一次性 logcat 快照。"""
        max_summary_lines = 2000
        max_process_lines = 20000

        lv = str(level or "W").upper().strip()
        if lv not in {"V", "D", "I", "W", "E", "F", "S"}:
            lv = "W"

        cmd = self.prefix + ["logcat", "-v", "threadtime"]

        ts: list[str] = []
        if tags:
            ts = [str(t).strip() for t in tags if t and str(t).strip()]
            if ts:
                for tag in ts:
                    cmd.append(f"{tag}:{lv}")
                cmd.append("*:S")
            else:
                cmd.append(f"*:{lv}")
        else:
            cmd.append(f"*:{lv}")

        cmd.append("-d")

        raw   = await Terminal.cmd_line(cmd)
        lines = (raw or "").splitlines()

        if len(lines) > max_process_lines:
            lines = lines[-max_process_lines:]

        ks: list[str] = []
        if keywords:
            ks = [str(k).strip() for k in keywords if k and str(k).strip()]

        if ks:
            pattern = re.compile("|".join(re.escape(k) for k in ks), re.IGNORECASE)
            lines = [ln for ln in lines if pattern.search(ln)]

        all_lines = list(lines)

        try:
            requested_max_lines = int(max_lines) if max_lines is not None else 200
        except (TypeError, ValueError):
            requested_max_lines = 200

        if requested_max_lines < 0:
            requested_max_lines = 0

        effective_max_lines = min(requested_max_lines, max_summary_lines)

        summary_lines = all_lines

        if 0 < effective_max_lines < len(summary_lines):
            summary_lines = summary_lines[-effective_max_lines:]
        elif effective_max_lines == 0:
            summary_lines = []

        content = "\n".join(summary_lines).strip()
        if not content:
            content = "logcat empty"

        attachments: list[Attachment] = []
        saved_path: typing.Optional[str] = None

        if saved and str(saved).strip():
            p = Path(str(saved)).expanduser()

            if p.suffix:
                out = p
            else:
                p.mkdir(parents=True, exist_ok=True)
                out = p / f"logcat_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.log"

            if not out.suffix:
                out = out.with_suffix(".log")

            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("\n".join(all_lines), const.CHARSET, const.IGNORE)
            saved_path = str(out.resolve())

            attachments.append(Attachment(
                kind="file",
                local=saved_path,
                filename=Path(saved_path).name,
                mime_type="text/plain"
            ))

        if saved_path:
            text = (
                f"logcat captured: {len(all_lines)} lines; "
                f"summary={len(summary_lines)} lines; saved={saved_path}"
            )
        else:
            text = (
                f"logcat captured: {len(all_lines)} lines; "
                f"summary={len(summary_lines)} lines"
            )

        return SemanticResult.from_text(
            text,
            attachments=attachments,
            data={
                "ok"      : True,
                "reason"  : None,
                "count"   : len(all_lines),
                "summary" : len(summary_lines),
                "saved"   : saved_path,
                "content" : content
            }
        ).to_dict()

    # workflow: ==== File Control MCP Tool ====
    async def file_logcat_clean(self, *_, **__) -> dict[str, typing.Any]:
        """清空 logcat 日志。"""
        cmd = self.prefix + ["logcat", "-c"]
        await Terminal.cmd_line(cmd)

        return SemanticResult.from_text(
            "logcat cleaned",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== Info Control MCP Tool ====
    async def grep_packages(
        self,
        keyword: typing.Optional[str] = None,
        scope: typing.Literal["user", "system", "all"] = "user"
    ) -> dict:
        """按范围和关键字筛选应用包名。"""
        def parse_pm_list_packages(text: str) -> list[str]:
            """解析 `pm list packages` 输出。"""
            pkg_list: list[str] = []
            for line in (text or "").splitlines():
                if not (line := line.strip()):
                    continue
                if line.startswith("package:"):
                    pkg_list.append(line.split("package:", 1)[1].strip())
                else:
                    pkg_list.append(line)

            # 去重保持顺序
            seen: set[str] = set()
            out: list[str] = []
            for pkg in pkg_list:
                if pkg and pkg not in seen:
                    seen.add(pkg)
                    out.append(pkg)
            return out

        kw = (keyword or "").strip()

        base = ["shell", "pm", "list", "packages"]
        match scope:
            case "user"   : base += ["-3"]
            case "system" : base += ["-s"]
            case "all"    : pass

        # 不传 keyword：直接列出
        if not kw:
            resp = await Terminal.cmd_line(self.prefix + base)
            pkgs = parse_pm_list_packages(resp)
            return SemanticResult.from_text(
                "\n".join(pkgs),
                data={"ok": True, "reason": None, "count": len(pkgs), "packages": pkgs}
            ).to_dict()

        # 传 keyword：过滤（保持当前 “| grep -i” 的写法）
        resp = await Terminal.cmd_line(self.prefix + base + [
            "|", "grep", "-i", kw
        ])
        pkgs = parse_pm_list_packages(resp)
        return SemanticResult.from_text(
            "\n".join(pkgs),
            data={"ok": True, "reason": None, "count": len(pkgs), "packages": pkgs}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def open_notification(self) -> typing.Any:
        """打开通知栏。"""
        cmd = self.prefix + [
            "shell", "cmd", "statusbar", "expand-notifications"
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "通知栏已打开。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def open_quick_settings(self) -> typing.Any:
        """打开快捷设置面板。"""
        cmd = self.prefix + [
            "shell", "cmd", "statusbar", "expand-settings"
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "快捷设置面板已打开。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def screen_on(self) -> dict[str, typing.Any]:
        """点亮屏幕。"""
        await self.screen(True)
        return SemanticResult.from_text(
            "屏幕已点亮。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def screen_off(self) -> dict[str, typing.Any]:
        """关闭屏幕。"""
        await self.screen(False)
        return SemanticResult.from_text(
            "屏幕已关闭。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def bluetooth_on(self) -> dict[str, typing.Any]:
        """打开蓝牙。"""
        await self._set_bluetooth("enable")
        return SemanticResult.from_text(
            "蓝牙已打开。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def bluetooth_off(self) -> dict[str, typing.Any]:
        """关闭蓝牙。"""
        await self._set_bluetooth("disable")
        return SemanticResult.from_text(
            "蓝牙已关闭。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def wifi_on(self) -> dict[str, typing.Any]:
        """打开 WiFi。"""
        await self._set_wifi("enable")
        return SemanticResult.from_text(
            "WiFi 已打开。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def wifi_off(self) -> dict[str, typing.Any]:
        """关闭 WiFi。"""
        await self._set_wifi("disable")
        return SemanticResult.from_text(
            "WiFi 已关闭。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def data_on(self) -> dict[str, typing.Any]:
        """打开移动数据。"""
        await self._set_mobile_data("enable")
        return SemanticResult.from_text(
            "移动数据已打开。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def data_off(self) -> dict[str, typing.Any]:
        """关闭移动数据。"""
        await self._set_mobile_data("disable")
        return SemanticResult.from_text(
            "移动数据已关闭。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def combo_key(self, first: int, others: list[int]) -> typing.Any:
        """执行组合按键。"""
        if not others:
            return SemanticResult.from_text(
                "未提供组合键。",
                data={"ok": False, "reason": "missing_others"}
            ).to_dict()

        commands = [f"input keyevent {other}" for other in others]

        shell_cmd = " ".join(
            self.prefix + ["shell", "input", "keyevent"]
        ) + f" --longpress {first} & sleep 0.03; " + "; ".join(commands)

        await Terminal.cmd_line_shell(shell_cmd)
        return SemanticResult.from_text(
            "组合按键已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def ime_reset(self) -> typing.Any:
        """重置为系统默认输入法。"""
        cmd = self.prefix + ["shell", "ime", "reset"]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "输入法已重置。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def reboot(
        self,
        mode: typing.Literal["", "recovery", "bootloader", "edl"] = "",
        wait: bool = False,
        wait_timeout: float = 120.0
    ) -> dict[str, typing.Any]:
        """重启设备。"""
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
                return SemanticResult.from_text(
                    "设备已触发重启，并已重新上线。",
                    data=data
                ).to_dict()
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                data["ok"] = False
                data["error"] = err
                data["wait_timeout"] = wait_timeout
                return SemanticResult.from_text(
                    f"设备已触发重启，但等待重新上线失败：{err}",
                    data=data
                ).to_dict()

        # 不等待：只表示命令已下发
        return SemanticResult.from_text(
            "已下发 adb reboot 指令。",
            data=data
        ).to_dict()

    # workflow: ==== System Control MCP Tool ====
    async def swipe_unlock(self) -> dict[str, typing.Any]:
        """点亮屏幕并上滑解锁。"""
        await self._screen(True)

        if not (wm := await self._wm_size()):
            return SemanticResult.from_text(
                "获取屏幕尺寸失败。",
                data={"ok": False, "reason": "wm_size_unavailable"}
            ).to_dict()

        w, h = wm

        x = w // 2
        y1 = int(h * 0.80)
        y2 = int(h * 0.35)

        await self.swipe(x, y1, x, y2, 1000)
        await asyncio.sleep(0.2)
        return SemanticResult.from_text(
            "已执行上滑解锁。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> typing.Any:
        """执行一次滑动手势。"""
        cmd = self.prefix + [
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "滑动已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_up(self, x: int, y: int, duration: int = 300) -> dict[str, typing.Any]:
        await self._scroll_by_direction("up", x, y, duration)
        return SemanticResult.from_text("向上滚动已执行。", data={"ok": True, "reason": None}).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_down(self, x: int, y: int, duration: int = 300) -> dict[str, typing.Any]:
        await self._scroll_by_direction("down", x, y, duration)
        return SemanticResult.from_text("向下滚动已执行。", data={"ok": True, "reason": None}).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_left(self, x: int, y: int, duration: int = 300) -> dict[str, typing.Any]:
        await self._scroll_by_direction("left", x, y, duration)
        return SemanticResult.from_text("向左滚动已执行。", data={"ok": True, "reason": None}).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_right(self, x: int, y: int, duration: int = 300) -> dict[str, typing.Any]:
        await self._scroll_by_direction("right", x, y, duration)
        return SemanticResult.from_text("向右滚动已执行。", data={"ok": True, "reason": None}).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_element_into_view(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        direction: typing.Literal["down", "up", "left", "right"] = "down",
        timeout: float = 12.0,
        max_swipes: int = 12,
        should_click: bool = False
    ) -> dict[str, typing.Any]:
        """将目标元素滚动到可见区域。"""
        result = await self._scroll_find(
            by, value, match, ignore_case, direction, timeout=timeout, max_swipes=max_swipes
        )
        # Device 只消费 Combo 的动作结果，不再关心滚动过程里的内部细节。
        matched = result.get("node")
        data = {
            "ok"     : bool(result.get("ok")),
            "reason" : result.get("reason"),
            "swipes" : result.get("swipes", 0)
        }

        if not result.get("ok"):
            reason = result.get("reason")
            return SemanticResult.from_text(self._scroll_find_text(reason), data=data).to_dict()

        if should_click and matched:
            center = getattr(matched, "center", None)
            if center and isinstance(center, (list, tuple)) and len(center) == 2:
                await self._tap(int(center[0]), int(center[1]))
                data["clicked"] = True
            else:
                data["clicked"] = False
                data["reason"] = "missing_center"

        if should_click:
            if data.get("clicked") is True:
                text = "已找到目标元素并完成点击。"
            elif matched:
                text = "已找到目标元素，但缺少可点击坐标。"
            else:
                text = "未找到目标元素。"
        else:
            text = "已找到目标元素。"

        return SemanticResult.from_text(text, data=data).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def click(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> dict[str, typing.Any]:
        """点击匹配到的目标元素。"""
        def pack_node(node: typing.Optional[Widget]) -> typing.Optional[dict[str, typing.Any]]:
            if not node:
                return None
            return {
                "id"     : node.id,
                "desc"   : node.desc,
                "text"   : node.text,
                "class"  : node.clazz,
                "center" : node.center,
                "bbox"   : node.bbox
            }

        if not (widget := await self._find_ui_widget(by, value, match, ignore_case)):
            return SemanticResult.from_text(
                "未找到可点击的节点。",
                data={"ok": False, "reason": "node_not_found", "node": None, "clicked": False}
            ).to_dict()

        if not widget.center:
            return SemanticResult.from_text(
                "找到节点但缺少可点击坐标（center）。",
                data={"ok": False, "reason": "missing_center", "node": pack_node(widget), "clicked": False}
            ).to_dict()

        await self._tap(*widget.center)

        return SemanticResult.from_text(
            "点击完成。",
            data={"ok": True, "reason": None, "node": pack_node(widget), "clicked": True}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def double_click(self, x: int, y: int) -> typing.Any:
        """在同一坐标执行双击。"""
        cmd = (
            " ".join(self.prefix)
            + f" shell input tap {x} {y}; sleep 0.08; input tap {x} {y}"
        )
        await Terminal.cmd_line_shell(cmd)
        return SemanticResult.from_text(
            "双击已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def key_event(self, keycode: int, longpress: bool = False) -> dict[str, typing.Any]:
        """发送系统按键。"""
        await self._send_keyevent(keycode, longpress)
        return SemanticResult.from_text(
            "按键已发送。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== Info Control MCP Tool ====
    async def screenshot(self, local: str) -> dict[str, typing.Any]:
        """保存截图到本地。"""
        saved = await self._save_screenshot(local)
        return SemanticResult.from_text(
            f"截图已保存到 {saved}",
            data={"ok": True, "reason": None, "path": saved}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def input_text(self, text: str) -> typing.Any:
        """向当前焦点输入文本。"""
        def sh_quote_single(s: str) -> str:
            """按单引号规则转义 shell 文本。"""
            return "'" + s.replace("'", r"'\''") + "'"

        if not (ime := await self._ime()).get("ok"):
            return ime

        text = "" if text is None else str(text)
        text = sh_quote_single(text)

        cmd = self.prefix + [
            "shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "文本输入已执行。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def clear_text(self) -> typing.Any:
        """清空当前焦点输入框文本。"""
        if not (ime := await self._ime()).get("ok"):
            return ime

        cmd = self.prefix + [
            "shell", "am", "broadcast", "-a", "ADB_CLEAR_TEXT"
        ]
        await Terminal.cmd_line(cmd)
        return SemanticResult.from_text(
            "文本已清空。",
            data={"ok": True, "reason": None}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_focus(self) -> dict[str, typing.Any]:
        """获取当前前台焦点。"""
        focus = await self._focus_info()
        package = focus.get("package")
        activity = focus.get("activity")
        raw = focus.get("raw", "")

        ok = bool(package or activity)

        return SemanticResult.from_text(
            f"当前Focus：package={package or ''} activity={activity or ''}".strip(),
            data={
                "ok"       : ok,
                "reason"   : None if ok else "parse_failed",
                "package"  : package,
                "activity" : activity,
                "raw"      : raw
            }
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_widgets(
        self,
        view: typing.Literal["interactive", "credible", "all"] = "all"
    ) -> dict[str, typing.Any]:
        """获取当前页面控件清单。"""
        def keep_node(w: Widget) -> bool:
            """按视图模式筛选控件。"""
            if view == "interactive":
                return any((w.clickable, w.focusable, w.scrollable))
            elif view == "credible":
                return any((w.id, w.desc, w.text))
            elif view == "all":
                return True

            return False

        if not (xml := await self._ui_xml()):
            return SemanticResult.from_text(
                "未获取到 UI XML。",
                data={"ok": False, "reason": "xml_unavailable", "count": 0}
            ).to_dict()

        widget_list = self._parse_widgets(xml)
        if not widget_list:
            return SemanticResult.from_text(
                "解析 UI XML 失败或页面为空。",
                data={"ok": False, "reason": "parse_failed", "count": 0}
            ).to_dict()

        lines: list[str] = [
            item.semantic for item in widget_list if keep_node(item)
        ]
        out = "\n".join(lines).strip()

        return SemanticResult.from_text(
            out if out else "未发现可用控件。",
            data={"ok": True, "reason": None, "count": len(lines)}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def find_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> dict[str, typing.Any]:
        """查找当前页面中的目标控件。"""
        if by == "xpath":
            return SemanticResult.from_text(
                " by=xpath 暂不支持（Android uiautomator dump 非标准 XPath）。",
                data={"ok": False, "reason": "xpath_not_supported", "node": None}
            ).to_dict()

        if found_node := await self._find_ui_widget(by, value, match, ignore_case):
            return SemanticResult.from_text(
                "已找到目标控件。",
                data={
                    "ok"     : True,
                    "reason" : None,
                    "node": {
                        "id"     : found_node.id,
                        "desc"   : found_node.desc,
                        "text"   : found_node.text,
                        "class"  : found_node.clazz,
                        "center" : found_node.center,
                        "bbox"   : found_node.bbox
                    }
                }
            ).to_dict()

        return SemanticResult.from_text(
            "未找到目标控件。",
            data={"ok": False, "reason": "node_not_found", "node": None}
        ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def heal_element(self, locator: str, *_, **__) -> dict[str, typing.Any]:
        """执行元素自愈流程。"""
        # 这是给 enhancer 的专用裸 payload，刻意不走 SemanticResult。
        page_id, page_dump, wm = await asyncio.gather(
            self.current_focus(), self._ui_xml(), self._wm_size()
        )
        w, h = wm if wm else (0, 0)
        payload = {
            "serial"    : self.serial,
            "page_id"   : page_id.get("data", {}).get("package") or "",
            "station"   : "android",
            "locator"   : locator,
            "page_dump" : page_dump or "",
            "wm_size"   : {"w": w, "h": h}
        }

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            new_local = await self._save_screenshot(tmp.name)
            with open(new_local, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            payload["screenshot_base64"]   = b64
            payload["screenshot_data_url"] = f"data:image/png;base64,{b64}"

        os.remove(new_local)

        return payload

    # workflow: ==== UI ====
    async def _wait_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 10.0,
        state: typing.Literal["exists", "gone"] = "exists"
    ) -> dict[str, typing.Any]:
        """等待节点出现或消失。"""
        # 等待逻辑沉到私有 helper，对外只暴露 wait_exists / wait_gone 两个工具语义入口。
        want_exists = (state == "exists")
        deadline    = time.monotonic() + float(timeout)
        found       = False

        while True:
            observed_node = await self._find_ui_widget(by, value, match, ignore_case)
            found = bool(observed_node)
            if found == want_exists:
                return SemanticResult.from_text(
                    "等待节点成功（已出现）。" if want_exists else "等待节点成功（已消失）。",
                    data={
                        "ok"     : True,
                        "reason" : None,
                        "found"  : found,
                        "node": {
                            "id"     : observed_node.id,
                            "desc"   : observed_node.desc,
                            "text"   : observed_node.text,
                            "class"  : observed_node.clazz,
                            "center" : observed_node.center,
                            "bbox"   : observed_node.bbox
                        } if observed_node else None
                    }
                ).to_dict()

            if time.monotonic() >= deadline:
                return SemanticResult.from_text(
                    "等待节点超时（未出现）。" if want_exists else "等待节点超时（未消失）。",
                    data={"ok": False, "reason": "timeout", "found": found, "node": None}
                ).to_dict()

            await asyncio.sleep(0.25)

    # workflow: ==== UI ====
    async def _scroll_to_edge(self, edge: typing.Literal["top", "bottom"]) -> dict[str, typing.Any]:
        """滑动到页面边界。"""
        # 这是内部滚动流程 helper，对外工具层只看到 scroll_to_top / scroll_to_bottom。
        max_swipes: int = 30

        duration_ms: int = 450
        settle_ms: int   = 450

        similarity_threshold: float = 0.992  # 相似度阈值
        stable_required: int        = 3      # 连续 3 次相似才停
        min_swipes_before_stop: int = 2      # 至少滑 2 次后才允许停

        x_ratio: float     = 0.5
        upper_ratio: float = 0.20  # 更靠近边缘一点 => 滑动更明显
        lower_ratio: float = 0.80

        if not (wm := await self._wm_size()):
            return SemanticResult.from_text(
                "获取屏幕尺寸失败。",
                data={"ok": False, "reason": "wm_size_unavailable", "swipes": 0}
            ).to_dict()

        w, h = wm
        x = int(w * x_ratio)

        y_upper = int(h * upper_ratio)
        y_lower = int(h * lower_ratio)

        if edge == "top":
            y_from, y_to = y_upper, y_lower  # 👇 swipe down (回到更上面)
        else:
            y_from, y_to = y_lower, y_upper  # 👆 swipe up (去到更下面)

        last_sim    = 0.0
        stable_hits = 0

        with tempfile.TemporaryDirectory(prefix="scroll_caps_") as tmp:
            tmp_dir   = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path  = str(tmp_dir / "cur.png")

            prev = await self._save_screenshot(prev_path)

            for n in range(1, max_swipes + 1):
                await self.swipe(x, y_from, x, y_to, duration_ms)
                await asyncio.sleep(settle_ms / 1000)

                cur = await self._save_screenshot(cur_path)
                last_sim = float(similarity(prev, cur))

                # 防抖：累计连续“几乎不变”的次数
                if last_sim >= similarity_threshold:
                    stable_hits += 1
                else:
                    stable_hits = 0

                # 至少滑动几次后，且连续 stable_required 次相似才停
                if n >= min_swipes_before_stop and stable_hits >= stable_required:
                    return SemanticResult.from_text(
                        "屏幕已稳定（内容未变化），停止滑动。",
                        data={"ok": True, "reason": "screen_not_changed", "swipes": n}
                    ).to_dict()

                prev, cur = cur, prev
                prev_path, cur_path = cur_path, prev_path

            return SemanticResult.from_text(
                "已达到最大滑动次数，停止滑动。",
                data={"ok": True, "reason": "max_swipes_reached", "swipes": max_swipes}
            ).to_dict()

    # workflow: ==== UI Interaction MCP Tool ====
    async def wait_exists(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 10.0
    ) -> dict[str, typing.Any]:
        return await self._wait_element(by, value, match, ignore_case, timeout, state="exists")

    # workflow: ==== UI Interaction MCP Tool ====
    async def wait_gone(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 10.0
    ) -> dict[str, typing.Any]:
        return await self._wait_element(by, value, match, ignore_case, timeout, state="gone")

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_to_top(self) -> dict[str, typing.Any]:
        return await self._scroll_to_edge("top")

    # workflow: ==== UI Interaction MCP Tool ====
    async def scroll_to_bottom(self) -> dict[str, typing.Any]:
        return await self._scroll_to_edge("bottom")


if __name__ == '__main__':
    pass
