# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import uuid
import typing
import asyncio
import tempfile
import contextlib
from pathlib import Path
from backend.mcp_hub.hub_device.phone import Phone
from backend.mcp_hub.hub_device.vision import similarity
from backend.models.model_device import (
    ActionResult, Attachment, SemanticResult
)


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
        remote   = "/data/local/tmp/" + filename

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

        return str(destination)

    async def wait_foreground(
        self,
        package: str,
        wait_s: float,
        poll: float,
        stable_hits: int
    ) -> tuple[bool, dict[str, typing.Any], int]:
        """等待指定应用稳定进入前台。"""
        hit = 0
        last_focus: dict[str, typing.Any] = {
            "package": None, "activity": None, "raw": ""
        }

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
        t0   = time.time()
        poll = 0.25

        ok0, focus0, _ = await self.wait_foreground(package, 0.8, poll, 1)
        if ok0:
            result = {
                "stage"   : "already",
                "focus"   : focus0,
                "cost_ms" : int((time.time() - t0) * 1000)
            }
            return SemanticResult(
                ok=True,
                text="应用已在前台，无需拉起。",
                data=result
            ).to_dict()

        await self.phone.app_start(package, activity)
        ok1, focus1, _ = await self.wait_foreground(package, 8.0, poll, 2)
        if ok1:
            result = {
                "stage"   : "start",
                "focus"   : focus1,
                "cost_ms" : int((time.time() - t0) * 1000)
            }
            return SemanticResult(
                ok=True,
                text="应用已成功进入前台。",
                data=result
            ).to_dict()

        await self.phone.app_stop(package)
        await self.phone.app_start(package, activity)
        ok2, focus2, _ = await self.wait_foreground(package, 5.0, poll, 2)
        if ok2:
            result = {
                "stage"   : "retry",
                "focus"   : focus2,
                "cost_ms" : int((time.time() - t0) * 1000)
            }
            return SemanticResult(
                ok=True,
                text="首次拉起未命中前台，重试后已进入前台。",
                data=result
            ).to_dict()

        result = {
            "stage"   : "retry",
            "focus"   : focus2,
            "cost_ms" : int((time.time() - t0) * 1000)
        }
        return SemanticResult(
            ok=False,
            text="拉起应用超时（已重试一次仍失败）。",
            data=result
        ).to_dict()

    async def screen_set(self, on: bool, settle: float = 0.2) -> None:
        """设置屏幕电源状态。"""
        if on == await self.phone.is_screen_on():
            return None

        await self.phone.send_keyevent(26)
        await asyncio.sleep(settle)

    async def set_screen(self, on: bool) -> dict[str, typing.Any]:
        """设置屏幕开关。"""
        await self.screen_set(on)
        return SemanticResult(
            ok=True,
            text="屏幕已点亮。" if on else "屏幕已关闭。",
            data={"on": on}
        ).to_dict()

    async def ensure_ime(self) -> dict[str, typing.Any]:
        """确保当前输入法为 AdbIME。"""
        ime = "com.android.adbkeyboard/.AdbIME"
        if await self.phone.ime_current() == ime:
            return SemanticResult(
                ok=True,
                text="AdbIME 已就绪。",
                data={}
            ).to_dict()

        e_text = await self.phone.ime_enable(ime)
        s_text = await self.phone.ime_set(ime)

        merged = f"{e_text}\n{s_text}".lower()

        if "unknown" in merged or "cannot" in merged:
            stage: list[str] = []
            if "unknown" in e_text.lower() or "cannot" in e_text.lower():
                stage.append("enable")
            if "unknown" in s_text.lower() or "cannot" in s_text.lower():
                stage.append("set")
            detail = f"（失败阶段：{', '.join(stage)}）" if stage else ""
            return SemanticResult(
                ok=False,
                text=f"AdbIME 不可用{detail}。",
                data={"stage": stage}
            ).to_dict()

        if await self.phone.ime_current() == ime:
            return SemanticResult(
                ok=True,
                text="AdbIME 已就绪。",
                data={}
            ).to_dict()

        return SemanticResult(
            ok=False,
            text="AdbIME 未生效。",
            data={}
        ).to_dict()

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
        deadline    = time.monotonic() + float(timeout)

        while True:
            node  = await self.phone.find_ui_widget(by, value, match, ignore_case)
            found = bool(node)
            if found == want_exists:
                result = {
                    "found"  : found,
                    "node"   : node.to_node() if node else None
                }
                return SemanticResult(
                    ok=True,
                    text="等待节点成功（已出现）。" if state == "exists" else "等待节点成功（已消失）。",
                    data=result
                ).to_dict()

            if time.monotonic() >= deadline:
                result = {
                    "found"  : found,
                    "node"   : None
                }
                return SemanticResult(
                    ok=False,
                    text="等待节点超时（未出现）。" if state == "exists" else "等待节点超时（未消失）。",
                    data=result
                ).to_dict()

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

        stable_hits = 0
        last_sim: float | None = None

        with tempfile.TemporaryDirectory(prefix="scroll_until_caps_") as tmp:
            tmp_dir   = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path  = str(tmp_dir / "cur.png")

            prev_ok = False
            if stop_on_stable:
                prev_path = await self.save_screenshot(prev_path)
                prev_ok = bool(prev_path)

            for i in range(1, int(max_swipes) + 1):
                if time.monotonic() >= deadline:
                    return ActionResult.fail(reason="timeout", swipes=i - 1)

                await self.phone.scroll_by_direction(direction, ax, ay, duration)

                await asyncio.sleep(float(settle))

                if widget := await self.phone.find_ui_widget(by, value, match, ignore_case):
                    return ActionResult.success(swipes=i, widget=widget)

                if stop_on_stable and prev_ok:
                    cur_saved = await self.save_screenshot(cur_path)
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
        action = await self.scroll_until(
            by=by,
            value=value,
            match=match,
            ignore_case=ignore_case,
            direction=direction,
            timeout=timeout,
            max_swipes=max_swipes
        )
        node = action.get("widget")

        data = {
            "swipes" : action.get("swipes", 0),
            "node"   : node.to_node() if node else None
        }

        if action.ok and should_click and node:
            center = getattr(node, "center", None)
            if center and isinstance(center, (list, tuple)) and len(center) == 2:
                await self.phone.tap(int(center[0]), int(center[1]))
                data["clicked"] = True
            else:
                data["clicked"] = False
                return SemanticResult(
                    ok=False,
                    text="已找到目标元素，但缺少可点击坐标。",
                    data=data
                ).to_dict()

        if not action.ok:
            if action.reason == "xpath_not_supported":
                text = "by=xpath 暂不支持（Android uiautomator dump 非标准 XPath）。"
            elif action.reason == "wm_size_unavailable":
                text = "获取屏幕尺寸失败。"
            elif action.reason == "timeout":
                text = "超时未找到目标元素。"
            elif action.reason == "scroll_fail":
                text = "滑动失败，已停止。"
            elif action.reason == "stable_stop":
                text = "屏幕内容稳定（几乎不变），停止滑动，仍未找到目标元素。"
            elif action.reason == "max_swipes_reached":
                text = "已达到最大滑动次数，仍未找到目标元素。"
            else:
                text = "滚动查找失败。"
            return SemanticResult(
                ok=False,
                text=text,
                data=data
            ).to_dict()

        if should_click:
            text = "已找到目标元素并完成点击。"
        else:
            text = "已找到目标元素。"

        return SemanticResult(
            ok=True,
            text=text,
            data=data
        ).to_dict()

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
            return SemanticResult(
                ok=False,
                text="获取屏幕尺寸失败。",
                data={"swipes": 0}
            ).to_dict()

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
            tmp_dir   = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path  = str(tmp_dir / "cur.png")

            prev = await self.save_screenshot(prev_path)

            for n in range(1, max_swipes + 1):
                await self.phone.swipe(x, y_from, x, y_to, duration_ms)
                await asyncio.sleep(settle_ms / 1000)

                cur = await self.save_screenshot(cur_path)

                if float(similarity(prev, cur)) >= similarity_threshold:
                    stable_hits += 1
                else:
                    stable_hits = 0

                if n >= min_swipes_before_stop and stable_hits >= stable_required:
                    return SemanticResult(
                        ok=True,
                        text="屏幕已稳定（内容未变化），停止滑动。",
                        data={"swipes": n}
                    ).to_dict()

                prev, cur = cur, prev
                prev_path, cur_path = cur_path, prev_path

            return SemanticResult(
                ok=True,
                text="已达到最大滑动次数，停止滑动。",
                data={"swipes": max_swipes}
            ).to_dict()

    async def swipe_unlock(self) -> dict[str, typing.Any]:
        """点亮屏幕并上滑解锁。"""
        await self.screen_set(True)

        if not (wm := await self.phone.wm_size()):
            return SemanticResult(
                ok=False,
                text="获取屏幕尺寸失败。",
                data={}
            ).to_dict()

        w, h = wm
        x = w // 2
        y1 = int(h * 0.80)
        y2 = int(h * 0.35)

        raw = await self.phone.swipe(x, y1, x, y2, 1000)
        await asyncio.sleep(0.2)
        return SemanticResult(
            ok=True,
            text="已执行上滑解锁。",
            data={"raw": raw}
        ).to_dict()

    async def screenshot(self, local: str) -> dict[str, typing.Any]:
        """保存截图到本地。"""
        saved = await self.save_screenshot(local)
        return SemanticResult(
            ok=True,
            text=f"截图已保存到 {saved}",
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


if __name__ == '__main__':
    pass
