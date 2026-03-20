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
from pathlib import Path
from backend.models.model_device import ActionResult
from .phone import Phone
from .vision import similarity
from engine.terminal import Terminal


class Combo(Phone):
    """设备组合能力层。"""

    def __init__(self, serial: str):
        super().__init__(serial)

    async def wait_fg(
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
            focus = await self.focus_info()
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

    async def screen(self, on: bool, settle: float = 0.2) -> None:
        """设置屏幕电源状态。"""
        # POWER 键本身是切换语义，因此先读状态，避免把幂等操作变成反向操作。
        if on == await self.is_screen_on():
            return None

        await self.send_keyevent(26)
        await asyncio.sleep(settle)

    async def ime(self) -> dict[str, typing.Any]:
        """确保当前输入法为 AdbIME。"""
        # 这是内部组合流程：检查 -> enable -> set -> 再校验，不属于 Device 工具语义层。
        ime = "com.android.adbkeyboard/.AdbIME"
        cmd = self.prefix + ["shell", "settings", "get", "secure", "default_input_method"]
        if (await Terminal.cmd_line(cmd) or "").strip() == ime:
            return ActionResult.success().to_dict()

        e_out = await Terminal.cmd_line(self.prefix + ["shell", "ime", "enable", ime])
        s_out = await Terminal.cmd_line(self.prefix + ["shell", "ime", "set", ime])

        e_text = "" if e_out is None else str(e_out)
        s_text = "" if s_out is None else str(s_out)
        merged = f"{e_text}\n{s_text}".lower()

        if "unknown" in merged or "cannot" in merged:
            stage: list[str] = []
            if "unknown" in e_text.lower() or "cannot" in e_text.lower():
                stage.append("enable")
            if "unknown" in s_text.lower() or "cannot" in s_text.lower():
                stage.append("set")
            return ActionResult.fail("ime_not_found", stage=stage).to_dict()

        current = (await Terminal.cmd_line(cmd)) or ""
        if current.strip() == ime:
            return ActionResult.success().to_dict()

        return ActionResult.fail("ime_not_effective").to_dict()

    async def scroll_find(
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

        if widget := await self.find_ui_widget(by, value, match, ignore_case):
            return pack(True, None, 0, widget)

        if not (wm := await self.wm_size()):
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

                try:
                    await self.scroll_by_direction(direction, ax, ay, duration=duration)
                except RuntimeError:
                    return pack(False, "scroll_fail", i - 1)

                await asyncio.sleep(float(settle))

                if widget := await self.find_ui_widget(by, value, match, ignore_case):
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


if __name__ == '__main__':
    pass
