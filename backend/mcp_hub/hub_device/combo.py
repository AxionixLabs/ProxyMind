#   ____                _
#  / ___|___  _ __ ___ | |__   ___
# | |   / _ \| '_ ` _ \| '_ \ / _ \
# | |__| (_) | | | | | | |_) | (_) |
#  \____\___/|_| |_| |_|_.__/ \___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import asyncio
import tempfile
import uuid
import contextlib
from pathlib import Path
from backend.models.model_device import ActionResult
from .phone import Phone
from .vision import similarity


class Combo(object):
    """设备组合能力层。"""

    def __init__(self, phone: Phone):
        self.phone = phone

    @property
    def serial(self) -> str:
        return self.phone.serial

    async def save_screenshot(self, local: str) -> str:
        """保存截图到本地路径。"""
        filename = f"screenshot_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        remote = "/data/local/tmp/" + filename

        await self.phone.screencap(remote)

        if (p := Path(local)).suffix:
            destination = p.with_name(f"{p.stem}_{self.serial}{p.suffix}")
        else:
            destination = p / f"screenshot_{self.serial}_{uuid.uuid4().hex[:6]}.png"

        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        await self.phone.file_pull(remote, str(destination))

        with contextlib.suppress(Exception):
            await self.phone.file_remove(remote)

        if not destination.exists():
            raise FileNotFoundError(f"screenshot pull failed: {destination}")

        return str(destination)

    async def wait_foreground(
        self,
        package: str,
        wait_s: float,
        poll: float,
        stable_hits: int
    ) -> tuple[bool, dict[str, typing.Any], int]:
        """等待指定应用稳定进入前台。"""
        # 组合层负责“等待/重试/稳定命中”这类多步流程，而不是单条 adb 调用。
        hit = 0
        last_focus: dict[str, typing.Any] = {"package": None, "activity": None, "raw": ""}

        deadline = time.time() + float(wait_s)
        while time.time() < deadline:
            focus = await self.phone.focus_info()
            current_package = focus.get("package")
            last_focus = {
                "package"  : current_package,
                "activity" : focus.get("activity"),
                "raw"      : focus.get("raw", "")
            }

            if current_package == package:
                hit += 1
                if hit >= int(stable_hits):
                    return True, last_focus, hit
            else:
                hit = 0

            await asyncio.sleep(float(poll))

        return False, last_focus, hit

    async def app_foreground(
        self,
        package: str,
        activity: typing.Optional[str] = None
    ) -> dict[str, typing.Any]:
        """确保应用位于前台。"""
        t0 = time.time()
        poll = 0.25

        ok0, focus0, _ = await self.wait_foreground(package, 0.8, poll, 1)
        if ok0:
            return {"ok": True, "reason": None, "stage": "already", "focus": focus0, "cost_ms": int((time.time() - t0) * 1000)}

        await self.phone.app_start(package, activity)
        ok1, focus1, _ = await self.wait_foreground(package, 8.0, poll, 2)
        if ok1:
            return {"ok": True, "reason": None, "stage": "start", "focus": focus1, "cost_ms": int((time.time() - t0) * 1000)}

        await self.phone.app_stop(package)
        await self.phone.app_start(package, activity)
        ok2, focus2, _ = await self.wait_foreground(package, 5.0, poll, 2)
        if ok2:
            return {"ok": True, "reason": None, "stage": "retry", "focus": focus2, "cost_ms": int((time.time() - t0) * 1000)}

        return {"ok": False, "reason": "retry_timeout", "stage": "retry", "focus": focus2, "cost_ms": int((time.time() - t0) * 1000)}

    async def screen_set(self, on: bool, settle: float = 0.2) -> None:
        """设置屏幕电源状态。"""
        # POWER 键本身是切换语义，因此先读状态，避免把幂等操作变成反向操作。
        if on == await self.phone.is_screen_on():
            return None

        await self.phone.send_keyevent(26)
        await asyncio.sleep(settle)

    async def ensure_ime(self) -> dict[str, typing.Any]:
        """确保当前输入法为 AdbIME。"""
        # 这是内部组合流程：检查 -> enable -> set -> 再校验，不属于 Device 工具语义层。
        ime = "com.android.adbkeyboard/.AdbIME"
        if await self.phone.ime_current() == ime:
            return ActionResult.success().to_dict()

        e_text = await self.phone.ime_enable(ime)
        s_text = await self.phone.ime_set(ime)

        merged = f"{e_text}\n{s_text}".lower()

        if "unknown" in merged or "cannot" in merged:
            stage: list[str] = []
            if "unknown" in e_text.lower() or "cannot" in e_text.lower():
                stage.append("enable")
            if "unknown" in s_text.lower() or "cannot" in s_text.lower():
                stage.append("set")
            return ActionResult.fail("ime_not_found", stage=stage).to_dict()

        if await self.phone.ime_current() == ime:
            return ActionResult.success().to_dict()

        return ActionResult.fail("ime_not_effective").to_dict()

    async def wait_element(
        self,
        by: typing.Literal["id", "desc", "text", "bbox", "xpath"],
        value: str | list,
        match: typing.Literal["eq", "contains", "regex"] = "eq",
        ignore_case: bool = False,
        timeout: float = 10.0,
        state: typing.Literal["exists", "gone"] = "exists"
    ) -> dict[str, typing.Any]:
        """等待节点出现或消失。"""
        want_exists = (state == "exists")
        deadline = time.monotonic() + float(timeout)

        while True:
            node = await self.phone.find_ui_widget(by, value, match, ignore_case)
            found = bool(node)
            if found == want_exists:
                return {"ok": True, "reason": None, "found": found, "node": node}

            if time.monotonic() >= deadline:
                return {"ok": False, "reason": "timeout", "found": found, "node": None}

            await asyncio.sleep(0.25)

    async def scroll_until(
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
        """滑动查找目标元素。"""
        def pack(
            ok: bool,
            reason: typing.Optional[str],
            swipes: int,
            node: typing.Optional[typing.Any] = None
        ) -> dict[str, typing.Any]:
            # Combo 对外仍返回扁平动作结果，方便 Device 继续做语义封装。
            result = (
                ActionResult.success(swipes=swipes, node=node)
                if ok else
                ActionResult.fail(reason or "unknown", swipes=swipes, node=node)
            ).to_dict()
            payload = dict(result.get("data") or {})
            payload["ok"] = result.get("ok", False)
            payload["reason"] = result.get("reason")
            return payload

        if by == "xpath":
            return pack(False, "xpath_not_supported", 0)

        deadline = time.monotonic() + float(timeout)

        if widget := await self.phone.find_ui_widget(by, value, match, ignore_case):
            return pack(True, None, 0, widget)

        if not (wm := await self.phone.wm_size()):
            return pack(False, "wm_size_unavailable", 0)

        w, h = wm
        if anchor is None:
            ax = int(w * 0.5)
            ay = int(h * 0.55)
        else:
            ax, ay = anchor

        stable_hits = 0

        with tempfile.TemporaryDirectory(prefix="scroll_until_caps_") as tmp:
            tmp_dir = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path = str(tmp_dir / "cur.png")

            prev_ok = False
            if stop_on_stable:
                try:
                    # 用截图相似度判断“页面已经滑到尽头”，比只看控件数量更稳。
                    prev_path = await self.save_screenshot(prev_path)
                    prev_ok = True
                except OSError:
                    prev_ok = False

            for i in range(1, int(max_swipes) + 1):
                if time.monotonic() >= deadline:
                    return pack(False, "timeout", i - 1)

                await self.phone.scroll_by_direction(direction, ax, ay, duration=duration)

                await asyncio.sleep(float(settle))

                if widget := await self.phone.find_ui_widget(by, value, match, ignore_case):
                    return pack(True, None, i, widget)

                if stop_on_stable and prev_ok:
                    try:
                        cur_path = await self.save_screenshot(cur_path)
                        sim = float(similarity(prev_path, cur_path))

                        stable_hits = stable_hits + 1 if sim >= float(similarity_threshold) else 0

                        # 连续稳定命中达到阈值后停止，避免在边界页无意义死滑。
                        if i >= int(min_swipes_before_stop) and stable_hits >= int(stable_required):
                            return pack(False, "stable_stop", i)

                        prev_path, cur_path = cur_path, prev_path
                    except (OSError, TypeError, ValueError):
                        prev_ok = False

        return pack(False, "max_swipes_reached", int(max_swipes))

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
        """将目标元素滚动到可见区域。"""
        result = await self.scroll_until(by, value, match, ignore_case, direction, None, 320, 0.25, timeout, max_swipes)
        node = result.get("node")
        data = {
            "ok": bool(result.get("ok")),
            "reason": result.get("reason"),
            "swipes": result.get("swipes", 0),
            "node": node,
        }

        if should_click and node:
            center = getattr(node, "center", None)
            if center and isinstance(center, (list, tuple)) and len(center) == 2:
                await self.phone.tap(int(center[0]), int(center[1]))
                data["clicked"] = True
            else:
                data["clicked"] = False
                data["reason"] = "missing_center"

        return data

    async def scroll_to_edge(self, edge: typing.Literal["top", "bottom"]) -> dict[str, typing.Any]:
        """滑动到页面边界。"""
        max_swipes: int = 30

        duration_ms: int = 450
        settle_ms: int   = 450

        similarity_threshold: float = 0.992
        stable_required: int        = 3
        min_swipes_before_stop: int = 2

        x_ratio: float     = 0.5
        upper_ratio: float = 0.20
        lower_ratio: float = 0.80

        if not (wm := await self.phone.wm_size()):
            return {"ok": False, "reason": "wm_size_unavailable", "swipes": 0}

        w, h = wm
        x = int(w * x_ratio)

        y_upper = int(h * upper_ratio)
        y_lower = int(h * lower_ratio)

        if edge == "top":
            y_from, y_to = y_upper, y_lower
        else:
            y_from, y_to = y_lower, y_upper

        last_sim = 0.0
        stable_hits = 0

        with tempfile.TemporaryDirectory(prefix="scroll_caps_") as tmp:
            tmp_dir   = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path  = str(tmp_dir / "cur.png")

            prev = await self.save_screenshot(prev_path)

            for n in range(1, max_swipes + 1):
                await self.phone.swipe(x, y_from, x, y_to, duration_ms)
                await asyncio.sleep(settle_ms / 1000)

                cur = await self.save_screenshot(cur_path)
                last_sim = float(similarity(prev, cur))

                if last_sim >= similarity_threshold:
                    stable_hits += 1
                else:
                    stable_hits = 0

                if n >= min_swipes_before_stop and stable_hits >= stable_required:
                    return {"ok": True, "reason": "screen_not_changed", "swipes": n}

                prev, cur = cur, prev
                prev_path, cur_path = cur_path, prev_path

            return {"ok": True, "reason": "max_swipes_reached", "swipes": max_swipes}

    async def swipe_unlock(self) -> dict[str, typing.Any]:
        """点亮屏幕并上滑解锁。"""
        await self.screen_set(True)

        if not (wm := await self.phone.wm_size()):
            return {"ok": False, "reason": "wm_size_unavailable"}

        w, h = wm
        x = w // 2
        y1 = int(h * 0.80)
        y2 = int(h * 0.35)

        await self.phone.swipe(x, y1, x, y2, 1000)
        await asyncio.sleep(0.2)
        return {"ok": True, "reason": None}


if __name__ == '__main__':
    pass
