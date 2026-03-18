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
import urllib.parse
from pathlib import Path
import xml.etree.ElementTree as Et
from engine.terminal import Terminal
from backend.mcp_hub.hub_device import Phone
from backend.mcp_hub.hub_device import Widget
from backend.mcp_hub.hub_device.vision import similarity
from backend.utilities import const


class Device(Phone):
    """Device class."""

    def __init__(self, serial: str):
        super().__init__(serial)

    def __str__(self):
        return (
            f"<Device {self.brand} {self.model} "
            f"serial={self.serial} version={self.version} hardware={self.hardware} sdk={self.sdk} abi={self.abi} "
            f"locale={self.locale} timezone={self.timezone} debuggable={self.debuggable} secure={self.secure}>"
        )

    __repr__ = __str__

    # workflow: ==== Info Control MCP Tool ====
    async def device_snapshot(self) -> typing.Union[dict, str]:
        """采集并返回该设备当前字符串摘要。"""
        battery     = await self.st_battery()
        wm_size     = await self.st_wm_size()
        online      = await self.is_online()
        emulator    = await self.is_emulator()
        screen_lock = await self.is_screen_lock()
        screen_on   = await self.is_screen_on()

        information = self.device_info | {
            "battery"     : battery,
            "wm_size"     : {"w": wm_size[0], "h": wm_size[1]} if wm_size else None,
            "online"      : online,
            "emulator"    : emulator,
            "screen_lock" : screen_lock,
            "screen_on"   : screen_on
        }

        return self.device_semantics(information)["semantic_brief"]

    # workflow: ==== App Control MCP Tool ====
    async def app_deep_link(self, url: str) -> typing.Any:
        """通过深度链接启动指定的应用服务。"""
        cmd = self.prefix + [
            "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url
        ]
        return await Terminal.cmd_line_shell(" ".join(cmd))

    # workflow: ==== App Control MCP Tool ====
    async def app_start(self, package: str, activity: typing.Optional[str] = None) -> typing.Any:
        """启动指定 Android 应用，可选择精确启动 Activity 或默认 Launcher 入口。"""
        action   = "android.intent.action.MAIN"
        category = "android.intent.category.LAUNCHER"

        if activity:
            cmd = self.prefix + [
                "shell", "am", "start", "-a", action, "-c", category, "-n", f"{package}/{activity}"
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
    async def app_uninstall(self, package: str, keep_data: bool = False) -> typing.Any:
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

    # workflow: ==== App Control MCP Tool ====
    async def app_foreground(
        self,
        package: str,
        activity: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """确保应用在前台。"""

        attachments: list[dict[str, typing.Any]] = []
        logs: list[str] = []

        t0 = time.time()

        poll: float = 0.25

        quick_wait: float = 0.8
        quick_hits: int   = 1

        first_wait: float = 8.0
        first_hits: int   = 2

        retry_wait: float = 5.0
        retry_hits: int   = 2

        # 快速检查
        ok0, focus0, hit0 = await self.wait_foreground(package, quick_wait, poll, quick_hits)
        if ok0:
            return {
                "text"        : "应用已在前台，无需拉起。",
                "attachments" : attachments,
                "data": {
                    "ok"          : True,
                    "stage"       : "already",
                    "package"     : package,
                    "activity"    : activity,
                    "focus"       : focus0,
                    "stable_hits" : hit0,
                    "cost_ms"     : int((time.time() - t0) * 1000)
                },
                "logs": logs
            }

        # 首次拉起
        start_out_1 = await self.app_start(package, activity)

        ok1, focus1, hit1 = await self.wait_foreground(package, first_wait, poll, first_hits)
        if ok1:
            return {
                "text"        : "应用已成功进入前台。",
                "attachments" : attachments,
                "data": {
                    "ok"                   : True,
                    "stage"                : "start",
                    "package"              : package,
                    "activity"             : activity,
                    "start_out"            : start_out_1,
                    "focus"                : focus1,
                    "stable_hits"          : hit1,
                    "timeout"              : first_wait,
                    "poll"                 : poll,
                    "stable_hits_required" : first_hits,
                    "cost_ms"              : int((time.time() - t0) * 1000)
                },
                "logs": logs
            }

        # 默认重试一次
        stop_out = await self.app_stop(package)
        start_out_2 = await self.app_start(package, activity)

        ok2, focus2, hit2 = await self.wait_foreground(package, retry_wait, poll, retry_hits)
        if ok2:
            return {
                "text"        : "首次拉起未命中前台，重试后已进入前台。",
                "attachments" : attachments,
                "data": {
                    "ok"                   : True,
                    "stage"                : "retry",
                    "package"              : package,
                    "activity"             : activity,
                    "start_out"            : start_out_1,
                    "stop_out"             : stop_out,
                    "start_out_retry"      : start_out_2,
                    "focus"                : focus2,
                    "stable_hits"          : hit2,
                    "timeout"              : first_wait,
                    "retry_timeout"        : retry_wait,
                    "poll"                 : poll,
                    "stable_hits_required" : first_hits,
                    "cost_ms"              : int((time.time() - t0) * 1000)
                },
                "logs": logs
            }

        return {
            "text"        : "拉起应用超时（已重试一次仍失败）。",
            "attachments" : attachments,
            "data": {
                "ok"                   : False,
                "stage"                : "retry_timeout",
                "package"              : package,
                "activity"             : activity,
                "start_out"            : start_out_1,
                "stop_out"             : stop_out,
                "start_out_retry"      : start_out_2,
                "last_focus"           : focus2,
                "stable_hits_last"     : hit2,
                "timeout"              : first_wait,
                "retry_timeout"        : retry_wait,
                "poll"                 : poll,
                "stable_hits_required" : retry_hits,
                "cost_ms"              : int((time.time() - t0) * 1000)
            },
            "logs": logs
        }

    # workflow: ==== File Control MCP Tool ====
    async def file_pull(self, remote: str, local: str) -> str:
        """从设备拉取文件到本地（返回绝对路径）。"""
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

        return str(destination)

    # workflow: ==== File Control MCP Tool ====
    async def file_push(self, local: str, remote: str) -> typing.Any:
        """将本地文件推送到设备。"""
        cmd = self.prefix + [
            "push", local, remote
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== File Control MCP Tool ====
    async def file_remove(self, path: str) -> typing.Any:
        """删除设备上的文件。"""
        cmd = self.prefix + [
            "shell", "rm", "-f", path
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== File Control MCP Tool ====
    async def file_logcat_dump(
        self,
        keywords: typing.Optional[list[str]] = None,
        tags: typing.Optional[list[str]] = None,
        level: str = "W",
        max_lines: int = 200,
        saved: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """
        一次性拉取 logcat 快照；支持按 tags/level 预过滤，再按 keywords(不分大小写 OR) 二次过滤；
        无论是否 saved，均保留 max_lines 摘要；saved=目录/文件时额外保存过滤后的全量（受内部处理上限保护）。
        """
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

        raw_line_count = len(lines)
        process_truncated = False
        if len(lines) > max_process_lines:
            lines = lines[-max_process_lines:]
            process_truncated = True

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

        summary_lines     = all_lines
        summary_truncated = False
        max_lines_capped  = (requested_max_lines != effective_max_lines)

        if 0 < effective_max_lines < len(summary_lines):
            summary_truncated = True
            summary_lines = summary_lines[-effective_max_lines:]
        elif effective_max_lines == 0:
            summary_lines = []

        content = "\n".join(summary_lines).strip()
        if not content:
            content = "logcat empty"

        attachments: list[dict[str, typing.Any]] = []
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

            attachments.append({
                "kind"      : "file",
                "local"     : saved_path,
                "filename"  : Path(saved_path).name,
                "mime_type" : "text/plain"
            })

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

        return {
            "text"        : text,
            "attachments" : attachments,
            "data": {
                "raw_line_count"      : raw_line_count,
                "line_count"          : len(all_lines),
                "summary_lines"       : len(summary_lines),
                "requested_max_lines" : requested_max_lines,
                "effective_max_lines" : effective_max_lines,
                "max_summary_limit"   : max_summary_lines,
                "max_process_limit"   : max_process_lines,
                "max_lines_capped"    : max_lines_capped,
                "summary_truncated"   : summary_truncated,
                "process_truncated"   : process_truncated,
                "keywords"            : ks,
                "tags"                : ts,
                "level"               : lv,
                "saved"               : saved_path,
                "content"             : content
            },
            "logs": []
        }

    # workflow: ==== File Control MCP Tool ====
    async def file_logcat_clean(self, *_, **__) -> dict[str, typing.Any]:
        """清空日志。"""
        cmd = self.prefix + ["logcat", "-c"]
        raw = await Terminal.cmd_line(cmd)

        return {
            "text"        : "logcat cleaned",
            "attachments" : [],
            "data"        : {"ok": True, "raw": raw or ""},
            "logs"        : []
        }

    # workflow: ==== File ====
    async def file_logcat_link(self) -> asyncio.subprocess.Process:
        """读取日志。"""
        cmd = self.prefix + [
            "logcat", "-v", "threadtime"
        ]
        return await Terminal.cmd_link(cmd)

    # workflow: ==== Info Control MCP Tool ====
    async def grep_packages(
        self,
        keyword: typing.Optional[str] = None,
        scope: typing.Literal["user", "system", "all"] = "user"
    ) -> dict:
        """
        - scope="user"  : 仅第三方（用户安装）包（pm list packages -3）
        - scope="system": 仅系统包（pm list packages -s）
        - scope="all"   : 全部包（pm list packages）
        - keyword       : 是否在对应 scope 的结果里 grep -i 过滤
        """
        def parse_pm_list_packages(text: str) -> list[str]:
            """
            pm list packages 输出：package:com.xxx
            解析成 ["com.xxx", ...]
            """
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
            resp = await Terminal.cmd_line(cmd := self.prefix + base)
            pkgs = parse_pm_list_packages(resp)
            return {
                "text"        : "\n".join(pkgs),
                "attachments" : [],
                "data": {
                    "cmd"      : cmd,
                    "keyword"  : None,
                    "scope"    : scope,
                    "count"    : len(pkgs),
                    "packages" : pkgs
                },
                "logs": []
            }

        # 传 keyword：过滤（保持当前 “| grep -i” 的写法）
        cmd = self.prefix + base + [
            "|", "grep", "-i", kw
        ]
        resp = await Terminal.cmd_line(cmd)
        pkgs = parse_pm_list_packages(resp)
        return {
            "text"        : "\n".join(pkgs),
            "attachments" : [],
            "data": {
                "cmd"      : cmd,
                "keyword"  : kw,
                "scope"    : scope,
                "count"    : len(pkgs),
                "packages" : pkgs
            },
            "logs": []
        }

    # workflow: ==== System Control MCP Tool ====
    async def open_notification(self) -> typing.Any:
        """打开通知栏（Notification Panel）。"""
        cmd = self.prefix + [
            "shell", "cmd", "statusbar", "expand-notifications"
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System Control MCP Tool ====
    async def open_settings(self) -> typing.Any:
        """打开快速设置面板（Quick Settings Panel）。"""
        cmd = self.prefix + [
            "shell", "cmd", "statusbar", "expand-settings"
        ]
        return await Terminal.cmd_line(cmd)

    # workflow: ==== System Control MCP Tool ====
    async def combo_key(self, first: int, others: list[int]) -> typing.Any:
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
    async def reboot(
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
                    "data"        : data,
                    "logs"        : []
                }
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                data["ok"] = False
                data["error"] = err
                data["wait_timeout"] = wait_timeout
                return {
                    "text"        : f"设备已触发重启，但等待重新上线失败：{err}",
                    "attachments" : [],
                    "data"        : data,
                    "logs"        : []
                }

        # 不等待：只表示命令已下发
        return {
            "text"        : "已下发 adb reboot 指令。",
            "attachments" : [],
            "data"        : data,
            "logs"        : []
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

    # workflow: ==== UI Interaction MCP Tool ====
    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> typing.Any:
        """从起点滑动到终点。"""
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
    async def scroll_into_view(
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
        """把元素“滚到可见”。可选滚到后点击。"""
        result = await self.scroll_until(
            by, value, match, ignore_case, direction, timeout=timeout, max_swipes=max_swipes
        )

        attachments: list[dict[str, typing.Any]] = []
        logs: list[str] = []

        if not result.get("ok"):
            reason = result.get("reason")
            text = {
                "xpath_not_supported" : "by=xpath 暂不支持（Android uiautomator dump 非标准 XPath）。",
                "wm_size_unavailable" : "获取屏幕尺寸失败。",
                "timeout"             : "超时未找到目标元素。",
                "scroll_fail"         : "滑动失败，已停止。",
                "stable_stop"         : "屏幕内容稳定（几乎不变），停止滑动，仍未找到目标元素。",
                "max_swipes_reached"  : "已达到最大滑动次数，仍未找到目标元素。"
            }.get(reason, "滚动查找失败。")
            return {
                "text"        : text,
                "attachments" : attachments,
                "data"        : result,
                "logs"        : logs
            }

        widget = result.get("widget")
        data = {
            **result,
            "widget" : widget.semantic if widget else None,
            "node": {
                "id"     : widget.id,
                "desc"   : widget.desc,
                "text"   : widget.text,
                "class"  : widget.clazz,
                "center" : widget.center,
                "bbox"   : widget.bbox
            } if widget else None
        }

        if should_click:
            node = data.get("node") or {}
            center = node.get("center")
            if center and isinstance(center, (list, tuple)) and len(center) == 2:
                await self.tap(int(center[0]), int(center[1]))
                data["clicked"] = True
            else:
                data["clicked"] = False
                data["click_reason"] = "no_center"

        return {
            "text": "已找到目标元素。" if not should_click else (
                "已找到目标元素并完成点击。" if data.get("clicked") else "已找到目标元素，但缺少可点击坐标。"
            ),
            "attachments" : attachments,
            "data"        : data,
            "logs"        : logs
        }

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
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> dict[str, typing.Any]:
        """根据选择器（支持非精确匹配）点击对应节点中心点。"""
        attachments: list[dict[str, typing.Any]] = []
        logs: list[str] = []

        if not (widget := await self.find_widget(by, value, match, ignore_case)):
            return {
                "text"        : "未找到可点击的节点。",
                "attachments" : attachments,
                "data": {
                    "ok"          : False,
                    "reason"      : "node_not_found",
                    "by"          : by,
                    "value"       : value,
                    "match"       : match,
                    "ignore_case" : ignore_case
                },
                "logs": logs
            }

        if not widget.center:
            return {
                "text"        : "找到节点但缺少可点击坐标（center）。",
                "attachments" : attachments,
                "data": {
                    "ok"          : False,
                    "reason"      : "missing_center",
                    "by"          : by,
                    "value"       : value,
                    "match"       : match,
                    "ignore_case" : ignore_case,
                    "bbox"        : widget.bbox
                },
                "logs": logs
            }

        out = await self.tap(*widget.center)

        return {
            "text"        : "点击完成。",
            "attachments" : attachments,
            "data": {
                "ok"          : True,
                "by"          : by,
                "value"       : value,
                "match"       : match,
                "ignore_case" : ignore_case,
                "center"      : widget.center,
                "bbox"        : widget.bbox,
                "tap_out"     : out
            },
            "logs": logs
        }

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
    async def send_keys_fallback(self, text: str) -> dict[str, typing.Any]:
        """降级输入：直接使用 adb shell input text。返回多模态结构。"""
        def escape(s: str) -> str:
            """
            adb shell input text 转义：
            - 空格 => %s
            - 其它字符尽量做 URL 编码（多数 ROM 可用）
            """
            s = s.replace(" ", "%s")
            return urllib.parse.quote(s, safe="%._-~:/@")

        attachments: list[dict[str, typing.Any]] = []

        if not (raw := "" if text is None else str(text)):
            return {
                "text"        : "输入内容为空，已跳过。",
                "attachments" : attachments,
                "data": {
                    "ok"      : True,
                    "method"  : "send_keys_fallback",
                    "skipped" : True
                },
                "logs": []
            }

        cmd = self.prefix + [
            "shell", "input", "text", escaped := escape(raw)
        ]

        out = await Terminal.cmd_line(cmd)
        out_text = str(out).strip() if out else ""

        low = out_text.lower()
        ok = not any(k in low for k in ("error", "exception", "not found", "invalid"))

        return {
            "text"        : "已使用降级输入（adb input text）。" if ok else "降级输入失败（adb input text）。",
            "attachments" : attachments,
            "data": {
                "ok"        : ok,
                "method"    : "send_keys_fallback",
                "input_len" : len(raw),
                "escaped"   : escaped
            },
            "logs": []
        }

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
        package, activity, raw = await self.focus()

        ok = bool(package or activity)

        return {
            "text"        : f"当前Focus：package={package or ''} activity={activity or ''}".strip(),
            "attachments" : [],
            "data": {
                "ok"       : ok,
                "stage"    : "parse",
                "reason"   : None if ok else "parse_failed",
                "package"  : package,
                "activity" : activity,
                "raw"      : raw
            },
            "logs": []
        }

    # workflow: ==== UI Interaction MCP Tool ====
    async def current_widgets(
        self,
        view: typing.Literal["interactive", "credible", "all"] = "all"
    ) -> dict[str, typing.Any]:
        """Dump 当前页面 XML -> 解析为 Widget 列表 -> 按 view 过滤 -> 输出语义化控件清单文本。"""
        def keep_node(w: Widget) -> bool:
            """根据 view 选择保留哪些控件：interactive=可交互；credible=有有效标识；all=全量。"""
            if view == "interactive":
                return any((w.clickable, w.focusable, w.scrollable))
            elif view == "credible":
                return any((w.id, w.desc, w.text))
            elif view == "all":
                return True

            return False

        attachments: list[dict[str, typing.Any]] = []
        logs: list[str] = []

        if not (xml := await self.current_xml()):
            return {
                "text"        : "未获取到 UI XML。",
                "attachments" : attachments,
                "data"        : {"ok": False},
                "logs"        : logs
            }

        xml = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", xml)

        try:
            root = Et.fromstring(xml)
        except Exception as e:
            return {
                "text"        : f"解析 UI XML 失败：{type(e).__name__}: {e}",
                "attachments" : attachments,
                "data"        : {"ok": False},
                "logs"        : logs
            }

        widget_list = [
            Widget(node.attrib) for node in root.iter("node")
        ]
        lines: list[str] = [
            widget.semantic for widget in widget_list if keep_node(widget)
        ]
        out = "\n".join(lines).strip()

        return {
            "text"        : out if out else "未发现可用控件。",
            "attachments" : attachments,
            "data": {
                "ok"        : True,
                "count"     : len(lines),
                "count_all" : len(widget_list)
            },
            "logs": logs
        }

    # workflow: ==== UI Interaction MCP Tool ====
    async def find_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False
    ) -> dict[str, typing.Any]:
        """在当前页面控件列表中查找目标控件；成功返回 widget 语义摘要与基础信息，失败返回原因与入参回显。"""
        if by == "xpath":
            return {
                "text"        : " by=xpath 暂不支持（Android uiautomator dump 非标准 XPath）。",
                "attachments" : [],
                "data": {
                    "ok"    : False,
                    "by"    : by,
                    "value" : value,
                    "match" : match
                },
                "logs": []
            }

        # 命中：回传语义摘要 + 关键定位/动作信息（用于 click/wait/诊断）
        if widget := await self.find_widget(by, value, match, ignore_case):
            return {
                "text"        : "已找到目标控件。",
                "attachments" : [],
                "data": {
                    "ok"     : True,
                    "by"     : by,
                    "value"  : value,
                    "match"  : match,
                    "widget" : widget.semantic
                },
                "logs": []
            }

        # 未命中：保持统一结构，回显查找参数
        return {
            "text"        : "未找到目标控件。",
            "attachments" : [],
            "data": {
                "ok"    : False,
                "by"    : by,
                "value" : value,
                "match" : match
            },
            "logs": []
        }

    # workflow: ==== UI Interaction MCP Tool ====
    async def heal_element(self, locator: str, *_, **__) -> dict[str, typing.Any]:
        """执行自愈流程定位并处理目标控件。"""
        page_id, page_dump, (w, h) = await asyncio.gather(
            self.current_focus(), self.current_xml(), self.st_wm_size()
        )
        payload = {
            "serial"    : self.serial,
            "page_id"   : page_id.get("data", {}).get("package") or "",
            "station"   : "android",
            "locator"   : locator,
            "page_dump" : page_dump or "",
            "wm_size"   : {"w": w, "h": h}
        }

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            new_local = await self.screenshot(tmp.name)
            with open(new_local, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            payload["screenshot_base64"]   = b64
            payload["screenshot_data_url"] = f"data:image/png;base64,{b64}"

        os.remove(new_local)

        return payload

    # workflow: ==== UI ====
    async def wait_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 10.0,
        state: typing.Literal["exists", "gone"] = "exists"
    ) -> dict[str, typing.Any]:
        """等待节点出现/消失。"""
        want_exists = (state == "exists")
        deadline    = time.monotonic() + float(timeout)

        while True:
            if (found := bool(await self.find_widget(by, value, match, ignore_case))) == want_exists:
                return {
                    "text"        : "等待节点成功（已出现）。" if want_exists else "等待节点成功（已消失）。",
                    "attachments" : [],
                    "data": {
                        "ok"          : True,
                        "by"          : by,
                        "value"       : value,
                        "match"       : match,
                        "ignore_case" : ignore_case,
                        "timeout"     : timeout,
                        "found"       : found
                    },
                    "logs": []
                }

            if time.monotonic() >= deadline:
                return {
                    "text"        : "等待节点超时（未出现）。" if want_exists else "等待节点超时（未消失）。",
                    "attachments" : [],
                    "data": {
                        "ok"          : False,
                        "by"          : by,
                        "value"       : value,
                        "match"       : match,
                        "ignore_case" : ignore_case,
                        "timeout"     : timeout,
                        "found"       : found,
                        "reason"      : "timeout"
                    },
                    "logs": []
                }

            await asyncio.sleep(0.25)

    # workflow: ==== UI ====
    async def ensure_ime(self) -> dict[str, typing.Any]:
        """切换到 AdbIME；若 enable/set 任一提示 Unknown input method，则直接返回错误结果。"""
        ime = "com.android.adbkeyboard/.AdbIME"
        cmd = self.prefix + ["shell", "settings", "get", "secure", "default_input_method"]
        if (await Terminal.cmd_line(cmd) or "").strip() == ime:
            return {
                "text"        : "当前已是 AdbIME 输入法",
                "attachments" : [],
                "data"        : {"ok": True},
                "logs"        : []
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
                },
                "logs": []
            }

        current = (await Terminal.cmd_line(cmd)) or ""

        if current.strip() == ime:
            return {
                "text"        : "已切换到 AdbIME",
                "attachments" : [],
                "data"        : {"ok": True},
                "logs"        : []
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
            },
            "logs": []
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
                last_sim = float(similarity(prev, cur))

                # 防抖：累计连续“几乎不变”的次数
                if last_sim >= similarity_threshold:
                    stable_hits += 1
                else:
                    stable_hits = 0

                # 至少滑动几次后，且连续 stable_required 次相似才停
                if n >= min_swipes_before_stop and stable_hits >= stable_required:
                    return {
                        "text"        : "屏幕已稳定（内容未变化），停止滑动。",
                        "attachments" : [],
                        "data": {
                            "ok"          : True,
                            "edge"        : edge,
                            "gesture"     : gesture,
                            "swipes"      : n,
                            "similarity"  : round(last_sim, 4),
                            "stable_hits" : stable_hits,
                            "reason"      : "screen_not_changed"
                        },
                        "logs": []
                    }

                prev, cur = cur, prev
                prev_path, cur_path = cur_path, prev_path

            return {
                "text"        : "已达到最大滑动次数，停止滑动。",
                "attachments" : [],
                "data": {
                    "ok"          : True,
                    "edge"        : edge,
                    "gesture"     : gesture,
                    "swipes"      : max_swipes,
                    "similarity"  : round(last_sim, 4),
                    "stable_hits" : stable_hits,
                    "reason"      : "max_swipes_reached"
                },
                "logs": []
            }


if __name__ == '__main__':
    pass
