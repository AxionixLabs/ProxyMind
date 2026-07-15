# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import re
import time
import uuid
import base64
import typing
import asyncio
import secrets
import tempfile
import contextlib
from pathlib import Path
from backend.models.model_device import (
    ActionResult,
    Attachment,
    SemanticResult
)
from backend.mcp_hub.hub_device.phone import Phone
from backend.mcp_hub.hub_device.vision import similarity
from backend.mcp_hub.hub_device.widget import Widget
from backend.utilities import const


class Device(object):
    """设备工具语义层。"""

    def __init__(self, serial: str):
        self.serial = serial

        self.agent_id: str = self.serial

        self.phone: Phone = Phone(serial)

    @property
    def device_props(self) -> dict[str, typing.Any]:
        return self.phone.device_props

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
    def device_semantics(device_snap: dict) -> dict:
        """构建设备快照的语义描述。"""

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
            f"{'online' if online else 'offline'} / "
            f"{'locked' if locked else 'unlocked'} / "
            f"{'screen on' if screen_on else 'screen off'} / "
            f"battery={battery_s or 'unknown'} / "
            f"screen={screen.replace('x', '×') if screen else 'unknown'} / "
            f"Android{device_snap.get('version') or '?'}(SDK{device_snap.get('sdk') or '?'}) / "
            f"{(device_snap.get('brand') or '').strip()} {(device_snap.get('model') or '').strip()}".strip()
        )

        return {
            "kv"    : semantic_kv,
            "brief" : semantic_brief
        }

    @staticmethod
    def _foreground_text(action: ActionResult) -> str:
        """生成前台切换结果文案。"""
        if not action.ok:
            return "App foregrounding timed out after one retry."

        stage = action.get("stage")
        if stage == "already":
            return "App is already in the foreground."
        if stage == "retry":
            return "App reached the foreground after a retry."

        return "App is in the foreground."

    @staticmethod
    def _ime_failure_result(action: ActionResult) -> dict[str, typing.Any]:
        """生成输入法失败结果。"""
        if action.reason == "ime_unavailable":
            stage  = action.get("stage", [])
            detail = f" (failed stages: {', '.join(stage)})" if stage else ""
            text   = f"AdbIME is unavailable{detail}."
        else:
            text = "AdbIME is not active."

        return SemanticResult(
            ok=False,
            text=text,
            data=action.data or {}
        ).to_dict()

    @staticmethod
    def _scroll_into_view_text(action: ActionResult) -> str:
        """生成滚动查找结果文案。"""
        if action.ok:
            return "Target element found."
        if action.reason == "xpath_not_supported":
            return "by=xpath is not supported because Android uiautomator dump is not standard XPath."
        if action.reason == "wm_size_unavailable":
            return "Unable to read screen dimensions."
        if action.reason == "timeout":
            return "Timed out while searching for the target element."
        if action.reason == "scroll_fail":
            return "Swipe failed; stopped searching."
        if action.reason == "stable_stop":
            return "Screen content remained stable; stopped swiping without finding the target element."
        if action.reason == "max_swipes_reached":
            return "Maximum swipe count reached; target element was not found."

        return "Failed to find the target element by scrolling."

    @staticmethod
    def _activity_candidates(package: str, activity: typing.Optional[str]) -> set[str]:
        """生成 Activity 等价匹配集合。"""
        raw = str(activity or "").strip()
        if not raw:
            return set()

        candidates: set[str] = {raw}
        if "/" in raw:
            owner, name = raw.split("/", 1)
            if name:
                candidates.add(name)
                if name.startswith("."):
                    candidates.add(f"{owner}{name}")
                    candidates.add(f"{package}{name}")
            return {item for item in candidates if item}

        candidates.add(f"{package}/{raw}")
        if raw.startswith("."):
            candidates.add(f"{package}{raw}")

        return {item for item in candidates if item}

    @classmethod
    def _focus_matches(
        cls,
        focus: dict[str, typing.Any],
        package: str,
        activity: typing.Optional[str] = None
    ) -> bool:
        """判断前台焦点是否命中目标包名和可选 Activity。"""
        if focus.get("package") != package:
            return False

        candidates = cls._activity_candidates(package, activity)
        if not candidates:
            return True

        current = str(focus.get("activity") or "").strip()
        if not current:
            return False

        values = {current}
        if "/" in current:
            _, name = current.split("/", 1)
            values.add(name)
            if name.startswith("."):
                values.add(f"{package}{name}")

        return bool(values & candidates)

    async def _save_screenshot(self, local: str) -> str:
        """保存截图到本地路径。"""
        filename = f"screenshot_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        remote   = "/data/local/tmp/" + filename

        await self.phone.screencap(remote)

        if (p := Path(local)).suffix:
            destination = p.with_name(f"{p.stem}_{self.serial}_{uuid.uuid4().hex[:6]}{p.suffix}")
        else:
            destination = p / f"screenshot_{self.serial}_{uuid.uuid4().hex[:6]}.png"

        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        await self.phone.file_pull(remote, str(destination))

        with contextlib.suppress(Exception):
            await self.phone.file_remove(remote)

        return str(destination)

    async def _wait_foreground(
        self,
        package: str,
        activity: typing.Optional[str],
        wait_s: float,
        poll: float,
        stable_hits: int
    ) -> tuple[bool, dict[str, typing.Any], int]:
        """等待指定应用稳定进入前台。"""
        hit: int = 0

        last_focus: dict[str, typing.Any] = {
            "package": None, "activity": None, "raw": ""
        }

        deadline = time.time() + float(wait_s)
        while time.time() < deadline:
            focus = await self.phone.focus_info()

            last_focus = {
                "package"  : focus.get("package"),
                "activity" : focus.get("activity"),
                "raw"      : focus.get("raw", "")
            }

            if self._focus_matches(last_focus, package, activity):
                hit += 1
                if hit >= int(stable_hits):
                    return True, last_focus, hit
            else:
                hit = 0

            await asyncio.sleep(float(poll))

        return False, last_focus, hit

    async def _ensure_foreground(
        self,
        package: str,
        activity: typing.Optional[str] = None
    ) -> ActionResult:
        """确保应用位于前台。"""
        t0 = time.time()

        poll: float = 0.25

        ok0, focus0, _ = await self._wait_foreground(package, activity, 0.8, poll, 1)
        if ok0:
            return ActionResult.success(
                stage="already",
                focus=focus0,
                cost_ms=int((time.time() - t0) * 1000)
            )

        await self.phone.app_start(package, activity)

        ok1, focus1, _ = await self._wait_foreground(package, activity, 8.0, poll, 2)
        if ok1:
            return ActionResult.success(
                stage="start",
                focus=focus1,
                cost_ms=int((time.time() - t0) * 1000)
            )

        await self.phone.app_stop(package)
        await self.phone.app_start(package, activity)

        ok2, focus2, _ = await self._wait_foreground(package, activity, 5.0, poll, 2)
        if ok2:
            return ActionResult.success(
                stage="retry",
                focus=focus2,
                cost_ms=int((time.time() - t0) * 1000)
            )

        return ActionResult.fail(
            "foreground_timeout",
            stage="retry",
            focus=focus2,
            cost_ms=int((time.time() - t0) * 1000)
        )

    async def _screen_set(self, on: bool, settle: float = 0.2) -> None:
        """设置屏幕电源状态。"""
        if on == await self.phone.is_screen_on():
            return None

        await self.phone.send_keyevent(26)
        await asyncio.sleep(settle)

    async def _ensure_ime(self) -> ActionResult:
        """确保当前输入法为 AdbIME。"""
        ime = "com.android.adbkeyboard/.AdbIME"
        if await self.phone.ime_current() == ime:
            return ActionResult.success()

        e_text = await self.phone.ime_enable(ime)
        s_text = await self.phone.ime_set(ime)

        merged = f"{e_text}\n{s_text}".lower()

        if "unknown" in merged or "cannot" in merged:
            stage: list[str] = []
            if "unknown" in e_text.lower() or "cannot" in e_text.lower():
                stage.append("enable")
            if "unknown" in s_text.lower() or "cannot" in s_text.lower():
                stage.append("set")
            return ActionResult.fail("ime_unavailable", stage=stage)

        if await self.phone.ime_current() == ime:
            return ActionResult.success()

        return ActionResult.fail("ime_not_applied")

    async def _locate_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 0.0
    ) -> ActionResult:
        """在当前页面定位目标节点，支持短轮询等待。"""
        if by == "xpath":
            return ActionResult.fail("xpath_not_supported", found=False, widget=None)

        wait_s   = max(0.0, float(timeout or 0.0))
        deadline = time.monotonic() + wait_s

        while True:
            if widget := await self.phone.find_ui_widget(by, value, match, ignore_case):
                return ActionResult.success(found=True, widget=widget)

            if wait_s <= 0.0:
                return ActionResult.fail("not_found", found=False, widget=None)

            if time.monotonic() >= deadline:
                return ActionResult.fail("timeout", found=False, widget=None)

            await asyncio.sleep(0.25)

    async def _scroll_until(
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
    ) -> ActionResult:
        """滑动查找目标元素，返回组合动作结果。"""
        if by == "xpath":
            return ActionResult.fail(reason="xpath_not_supported", swipes=0)

        if widget := await self.phone.find_ui_widget(by, value, match, ignore_case):
            return ActionResult.success(swipes=0, widget=widget)

        if not (wm := await self.phone.wm_size()):
            return ActionResult.fail(reason="wm_size_unavailable", swipes=0)

        deadline = time.monotonic() + float(timeout)

        w, h = wm
        if anchor is None:
            ax = int(w * 0.5)
            ay = int(h * 0.55)
        else:
            ax, ay = anchor

        stable_hits: int = 0

        last_sim: float | None = None

        with tempfile.TemporaryDirectory(prefix="scroll_until_caps_") as tmp:
            tmp_dir   = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path  = str(tmp_dir / "cur.png")

            prev_ok = False
            if stop_on_stable:
                prev_path = await self._save_screenshot(prev_path)
                prev_ok = bool(prev_path)

            for i in range(1, int(max_swipes) + 1):
                if time.monotonic() >= deadline:
                    return ActionResult.fail(reason="timeout", swipes=i - 1)

                await self.phone.scroll_by_direction(direction, ax, ay, duration)
                await asyncio.sleep(float(settle))

                if widget := await self.phone.find_ui_widget(by, value, match, ignore_case):
                    return ActionResult.success(swipes=i, widget=widget)

                if stop_on_stable and prev_ok:
                    cur_saved = await self._save_screenshot(cur_path)
                    if not cur_saved:
                        prev_ok = False
                    else:
                        cur_path = cur_saved
                        sim = float(similarity(prev_path, cur_path))
                        last_sim = sim

                        stable_hits = stable_hits + 1 if sim >= float(similarity_threshold) else 0

                        if i >= int(min_swipes_before_stop) and stable_hits >= int(stable_required):
                            return ActionResult.fail(reason="stable_stop", swipes=i, similarity=round(sim, 4))

                        prev_path, cur_path = cur_path, prev_path

        data: dict[str, typing.Any] = {"swipes": int(max_swipes)}
        if last_sim is not None:
            data["similarity"] = round(last_sim, 4)

        return ActionResult.fail(reason="max_swipes_reached", **data)

    async def _scroll_to_edge(
        self,
        edge: typing.Literal["top", "bottom"]
    ) -> ActionResult:
        """滑动到页面边界。"""
        max_swipes: int  = 30
        duration_ms: int = 450
        settle_ms: int   = 450

        similarity_threshold: float = 0.992
        stable_required: int        = 3
        min_swipes_before_stop: int = 2

        x_ratio: float = 0.5

        upper_ratio: float = 0.20
        lower_ratio: float = 0.80

        if not (wm := await self.phone.wm_size()):
            return ActionResult.fail("wm_size_unavailable", swipes=0)

        w, h = wm
        x = int(w * x_ratio)

        y_upper = int(h * upper_ratio)
        y_lower = int(h * lower_ratio)

        if edge == "top":
            y_from, y_to = y_upper, y_lower
        else:
            y_from, y_to = y_lower, y_upper

        stable_hits: int = 0

        with tempfile.TemporaryDirectory(prefix="scroll_caps_") as tmp:
            tmp_dir = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path = str(tmp_dir / "cur.png")

            prev = await self._save_screenshot(prev_path)

            for n in range(1, max_swipes + 1):
                await self.phone.swipe(x, y_from, x, y_to, duration_ms)
                await asyncio.sleep(settle_ms / 1000)

                cur = await self._save_screenshot(cur_path)

                if float(similarity(prev, cur)) >= similarity_threshold:
                    stable_hits += 1
                else:
                    stable_hits = 0

                if n >= min_swipes_before_stop and stable_hits >= stable_required:
                    return ActionResult.success(swipes=n, stop_reason="stable")

                prev, cur = cur, prev
                prev_path, cur_path = cur_path, prev_path

            return ActionResult.success(swipes=max_swipes, stop_reason="max_swipes")

    async def _swipe_unlock(self) -> ActionResult:
        """点亮屏幕并上滑解锁。"""
        await self._screen_set(True)

        if not (wm := await self.phone.wm_size()):
            return ActionResult.fail("wm_size_unavailable")

        w, h = wm
        x = w // 2
        y1 = int(h * 0.80)
        y2 = int(h * 0.35)

        raw = await self.phone.swipe(x, y1, x, y2, 1000)
        await asyncio.sleep(0.2)
        return ActionResult.success(raw=raw)

    async def refresh_device_props(self) -> dict[str, typing.Any]:
        """刷新并返回设备属性。"""
        return await self.phone.refresh_device_props()

    async def device_snapshot(self) -> dict[str, typing.Any]:
        """返回设备当前快照。"""
        props = await self.phone.refresh_device_props()

        battery     = await self.phone.battery()
        wm_size     = await self.phone.wm_size()
        online      = await self.phone.is_online()
        emulator    = await self.phone.is_emulator()
        screen_lock = await self.phone.is_screen_locked()
        screen_on   = await self.phone.is_screen_on()

        information = props | {
            "battery"     : battery,
            "wm_size"     : {"w": wm_size[0], "h": wm_size[1]} if wm_size else None,
            "online"      : online,
            "emulator"    : emulator,
            "screen_lock" : screen_lock,
            "screen_on"   : screen_on
        }
        semantic = self.device_semantics(information)

        return SemanticResult(
            ok=True,
            text=semantic["brief"],
            data={
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

    async def app_deep_link(self, url: str) -> typing.Any:
        """通过深度链接启动应用。"""
        raw = await self.phone.app_deep_link(url)
        return SemanticResult(
            ok=True,
            text="Deep link command executed.",
            data={"raw": raw}
        ).to_dict()

    async def app_stop(self, package: str) -> typing.Any:
        """强制停止指定应用。"""
        raw = await self.phone.app_stop(package)
        return SemanticResult(
            ok=True,
            text="Application stop command executed.",
            data={"raw": raw}
        ).to_dict()

    async def app_clear(self, package: str) -> typing.Any:
        """清除指定应用的数据。"""
        raw = await self.phone.app_clear(package)
        return SemanticResult(
            ok=True,
            text="Application data clear command executed.",
            data={"raw": raw}
        ).to_dict()

    async def app_foreground(
        self,
        package: str,
        activity: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """确保应用位于前台。"""
        action = await self._ensure_foreground(package, activity)
        return SemanticResult(
            ok=action.ok,
            text=self._foreground_text(action),
            data=action.data or {}
        ).to_dict()

    async def file_logcat_link(self) -> asyncio.subprocess.Process:
        """连接 logcat 流，供内部注入流程消费。"""
        return await self.phone.logcat_link()

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

        raw = await self.phone.logcat_dump(tags, level)
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

            attachments.append(
                Attachment(
                    kind="file",
                    local=saved_path,
                    filename=Path(saved_path).name,
                    mime_type="text/plain"
                )
            )

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

        return SemanticResult(
            ok=True,
            text=text,
            attachments=attachments,
            data={
                "count"   : len(all_lines),
                "summary" : len(summary_lines),
                "saved"   : saved_path,
                "content" : content
            }
        ).to_dict()

    async def file_logcat_clean(self, *_, **__) -> dict[str, typing.Any]:
        """清空 logcat 日志。"""
        raw = await self.phone.logcat_clean()
        return SemanticResult(
            ok=True,
            text="logcat cleaned",
            data={"raw": raw}
        ).to_dict()

    async def grep_packages(
        self,
        keyword: typing.Optional[str] = None,
        scope: typing.Literal["user", "system", "all"] = "user"
    ) -> dict:
        """按范围和关键字筛选应用包名。"""
        kw = (keyword or "").strip()
        if not kw:
            resp = await self.phone.list_packages(scope)
            pkgs = self.phone.parse_package_list(resp)
            return SemanticResult(
                ok=True,
                text="\n".join(pkgs),
                data={"count": len(pkgs), "packages": pkgs}
            ).to_dict()

        resp = await self.phone.grep_packages(kw, scope)
        pkgs = self.phone.parse_package_list(resp)
        return SemanticResult(
            ok=True,
            text="\n".join(pkgs),
            data={"count": len(pkgs), "packages": pkgs}
        ).to_dict()

    async def open_notification(self) -> typing.Any:
        """打开通知栏。"""
        raw = await self.phone.open_notification()
        return SemanticResult(
            ok=True,
            text="Notification shade opened.",
            data={"raw": raw}
        ).to_dict()

    async def open_quick_settings(self) -> typing.Any:
        """打开快捷设置面板。"""
        raw = await self.phone.open_quick_settings()
        return SemanticResult(
            ok=True,
            text="Quick settings opened.",
            data={"raw": raw}
        ).to_dict()

    async def set_screen(self, on: bool) -> dict[str, typing.Any]:
        """设置屏幕开关。"""
        await self._screen_set(on)
        return SemanticResult(
            ok=True,
            text="Screen turned on." if on else "Screen turned off.",
            data={"on": on}
        ).to_dict()

    async def reboot(
        self,
        mode: typing.Literal["", "recovery", "bootloader", "edl"] = "",
        wait: bool = False,
        wait_timeout: float = 120.0
    ) -> dict[str, typing.Any]:
        """重启设备。"""
        raw = await self.phone.reboot(mode)

        data: dict[str, typing.Any] = {"raw": raw, "mode": mode}

        # 仅普通重启才支持 wait-for-device
        if wait and mode == "":
            try:
                await asyncio.wait_for(
                    self.phone.wait_for_device(), timeout=wait_timeout
                )
                return SemanticResult(
                    ok=True,
                    text="Device rebooted and reconnected.",
                    data=data
                ).to_dict()
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                data["error"] = err
                data["wait_timeout"] = wait_timeout
                return SemanticResult(
                    ok=False,
                    text=f"Device reboot command executed, but reconnect wait failed: {err}",
                    data=data
                ).to_dict()

        # 不等待：只表示命令已下发
        return SemanticResult(
            ok=True,
            text="ADB reboot command sent.",
            data=data
        ).to_dict()

    async def swipe_unlock(self) -> dict[str, typing.Any]:
        """点亮屏幕并上滑解锁。"""
        action = await self._swipe_unlock()
        if not action.ok:
            return SemanticResult(
                ok=False,
                text="Unable to read screen dimensions." if action.reason == "wm_size_unavailable" else "Unlock failed.",
                data=action.data or {}
            ).to_dict()

        return SemanticResult(
            ok=True,
            text="Swipe unlock executed.",
            data=action.data or {}
        ).to_dict()

    async def scroll_into_view(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        direction: typing.Literal["down", "up", "left", "right"] = "down",
        timeout: float = 12.0,
        max_swipes: int = 12,
    ) -> dict[str, typing.Any]:
        """将目标元素滚动到可见区域。"""
        action = await self._scroll_until(
            by=by,
            value=value,
            match=match,
            ignore_case=ignore_case,
            direction=direction,
            timeout=timeout,
            max_swipes=max_swipes
        )

        node = action.get("widget")

        return SemanticResult(
            ok=action.ok,
            text=self._scroll_into_view_text(action),
            data={
                "swipes" : action.get("swipes", 0),
                "node"   : node.to_node() if node else None
            }
        ).to_dict()

    async def click(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 3.0,
        scroll: bool = False,
        direction: typing.Literal["down", "up", "left", "right"] = "down",
        max_swipes: int = 12
    ) -> dict[str, typing.Any]:
        """点击匹配到的目标元素。"""
        located = await self._locate_element(by, value, match, ignore_case, timeout)
        if not located.ok and scroll:
            action = await self._scroll_until(
                by=by,
                value=value,
                match=match,
                ignore_case=ignore_case,
                direction=direction,
                timeout=max(12.0, float(timeout or 0.0)),
                max_swipes=max_swipes
            )
            if not action.ok:
                return SemanticResult(
                    ok=False,
                    text="Clickable node was not found after scrolling.",
                    data={
                        "node"    : None,
                        "clicked" : False,
                        "reason"  : action.reason,
                        "swipes"  : action.get("swipes", 0)
                    }
                ).to_dict()
            located = action

        if not located.ok:
            message = (
                "by=xpath is not supported because Android uiautomator dump is not standard XPath."
                if located.reason == "xpath_not_supported"
                else "Clickable node was not found."
            )
            return SemanticResult(
                ok=False,
                text=message,
                data={"node": None, "clicked": False, "reason": located.reason}
            ).to_dict()

        widget = located.get("widget")

        if not widget or not widget.center:
            return SemanticResult(
                ok=False,
                text="Target node has no clickable center coordinate.",
                data={"node": widget.to_node() if widget else None, "clicked": False}
            ).to_dict()

        raw = await self.phone.tap(*widget.center)

        return SemanticResult(
            ok=True,
            text="Tap completed.",
            data={"raw": raw, "node": widget.to_node(), "clicked": True}
        ).to_dict()

    async def key_event(self, keycode: int, longpress: bool = False) -> dict[str, typing.Any]:
        """发送系统按键。"""
        raw = await self.phone.send_keyevent(keycode, longpress)
        return SemanticResult(
            ok=True,
            text="Key event sent.",
            data={"raw": raw}
        ).to_dict()

    async def screenshot(self, local: str) -> dict[str, typing.Any]:
        """保存截图到本地。"""
        saved = await self._save_screenshot(local)
        return SemanticResult(
            ok=True,
            text=f"Screenshot saved to {saved}",
            attachments=[
                Attachment(
                    kind="image",
                    local=saved,
                    filename=Path(saved).name,
                    mime_type="image/png"
                )
            ],
            data={"path": saved}
        ).to_dict()

    async def input_text(
        self,
        text: str,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"] | None = None,
        value: str | list | None = None,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 3.0,
        replace: bool = False
    ) -> typing.Any:
        """向当前焦点输入文本。"""
        node: dict[str, typing.Any] | None = None
        tap_raw: typing.Any = None

        if (by is None) != (value is None):
            return SemanticResult(
                ok=False,
                text="Input locator parameters are incomplete.",
                data={"node": None, "input": False}
            ).to_dict()

        if by is not None:
            located = await self._locate_element(by, value, match, ignore_case, timeout)
            if not located.ok:
                message = (
                    "by=xpath is not supported because Android uiautomator dump is not standard XPath."
                    if located.reason == "xpath_not_supported"
                    else "Input target node was not found."
                )
                return SemanticResult(
                    ok=False,
                    text=message,
                    data={"node": None, "input": False, "reason": located.reason}
                ).to_dict()

            widget = located.get("widget")
            if not widget or not widget.center:
                return SemanticResult(
                    ok=False,
                    text="Input target node has no center coordinate.",
                    data={"node": widget.to_node() if widget else None, "input": False}
                ).to_dict()

            tap_raw = await self.phone.tap(*widget.center)
            node = widget.to_node()

        ime = await self._ensure_ime()

        if not ime.ok:
            return self._ime_failure_result(ime)

        clear_raw = None
        if replace:
            clear_raw = await self.phone.clear_text()

        raw = await self.phone.input_text("" if text is None else str(text))
        return SemanticResult(
            ok=True,
            text="Text input completed.",
            data={
                "raw"       : raw,
                "tap_raw"   : tap_raw,
                "clear_raw" : clear_raw,
                "node"      : node,
                "focused"   : node is not None,
                "replaced"  : bool(replace),
                "input"     : True
            }
        ).to_dict()

    async def clear_text(self) -> typing.Any:
        """清空当前焦点输入框文本。"""
        ime = await self._ensure_ime()

        if not ime.ok:
            return self._ime_failure_result(ime)

        raw = await self.phone.clear_text()
        return SemanticResult(
            ok=True,
            text="Text cleared.",
            data={"raw": raw}
        ).to_dict()

    async def current_focus(self) -> dict[str, typing.Any]:
        """获取当前前台焦点。"""
        focus    = await self.phone.focus_info()
        package  = focus.get("package")
        activity = focus.get("activity")
        raw      = focus.get("raw", "")

        ok = bool(package or activity)

        return SemanticResult(
            ok=ok,
            text=f"Current focus: package={package or ''} activity={activity or ''}".strip(),
            data={
                "package"  : package,
                "activity" : activity,
                "raw"      : raw
            }
        ).to_dict()

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

        if not (xml := await self.phone.ui_xml()):
            return SemanticResult(
                ok=False,
                text="UI XML was not available.",
                data={"count": 0}
            ).to_dict()

        widget_list = self.phone.parse_widgets(xml)
        if not widget_list:
            return SemanticResult(
                ok=True,
                text="No usable widgets are available on the current screen.",
                data={"count": 0}
            ).to_dict()

        lines: list[str] = [
            item.semantic for item in widget_list if keep_node(item)
        ]
        out = "\n".join(lines).strip()

        return SemanticResult(
            ok=True,
            text=out if out else "No usable widgets found.",
            data={"count": len(lines)}
        ).to_dict()

    async def heal_element(self, locator: str, *_, **__) -> dict[str, typing.Any]:
        """执行元素自愈流程。"""
        page_id, page_dump, wm = await asyncio.gather(
            self.current_focus(), self.phone.ui_xml(), self.phone.wm_size()
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

        return SemanticResult(
            ok=True,
            text="Element locator diagnostics.",
            data=payload
        ).to_dict()

    async def scroll_to_edge(self, edge: typing.Literal["top", "bottom"]) -> dict[str, typing.Any]:
        """滚动到边界。"""
        action = await self._scroll_to_edge(edge)
        if not action.ok:
            return SemanticResult(
                ok=False,
                text="Unable to read screen dimensions." if action.reason == "wm_size_unavailable" else "Failed to scroll to the edge.",
                data={"swipes": action.get("swipes", 0)}
            ).to_dict()

        text = (
            "Screen content remained stable; stopped swiping."
            if action.get("stop_reason") == "stable"
            else "Maximum swipe count reached; stopped swiping."
        )
        return SemanticResult(
            ok=True,
            text=text,
            data={"swipes": action.get("swipes", 0)}
        ).to_dict()

    async def monkey_stop(self) -> dict[str, typing.Any]:
        """向设备发送 monkey 停止命令，不等待确认结果。"""
        script = """
ps -A | grep com.android.commands.monkey | while read -r _ pid _; do
  kill -9 "$pid" 2>/dev/null || true
done
printf 'stop_sent=1\\n'
""".strip()
        await self.phone.shell_script(script)
        return SemanticResult(
            ok=True,
            text="Monkey stop command sent.",
            data={
                "reason": "stop_command_sent"
            }
        ).to_dict()


if __name__ == '__main__':
    pass
