#  ____  _                         ____                _
# |  _ \| |__   ___  _ __   ___   / ___|___  _ __ ___ | |__   ___
# | |_) | '_ \ / _ \| '_ \ / _ \ | |   / _ \| '_ ` _ \| '_ \ / _ \
# |  __/| | | | (_) | | | |  __/ | |__| (_) | | | | | | |_) | (_) |
# |_|   |_| |_|\___/|_| |_|\___|  \____\___/|_| |_| |_|_.__/ \___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
import asyncio
import tempfile
import urllib.parse
from pathlib import Path
from .phone import Phone
from .vision import similarity
from engine.terminal import Terminal


class PhoneCombo(Phone):
    """组合能力层"""

    def __init__(self, serial: str):
        super().__init__(serial)

    # workflow: ==== APP ====
    async def wait_foreground(
        self,
        package: str,
        wait_s: float,
        poll: float,
        stable_hits: int
    ) -> tuple[bool, dict[str, typing.Any], int]:
        """等待指定 package 进入前台（稳定命中 stable_hits 次）。"""
        hit = 0
        last_focus: dict[str, typing.Any] = {"package": None, "activity": None, "raw": ""}

        deadline = time.time() + float(wait_s)
        while time.time() < deadline:
            current_package, current_activity, raw = await self.focus()
            last_focus = {"package": current_package, "activity": current_activity, "raw": raw}

            if current_package == package:
                hit += 1
                if hit >= int(stable_hits):
                    return True, last_focus, hit
            else:
                hit = 0

            await asyncio.sleep(float(poll))

        return False, last_focus, hit

    # workflow: ==== System ====
    async def screen_set(self, on: bool, settle: float = 0.2) -> None:
        """统一控制屏幕电源态。"""
        if on == await self.is_screen_on():
            return None

        await self.key_event(26)
        await asyncio.sleep(settle)

    # workflow: ==== UI ====
    async def send_keys_fallback(self, text: str) -> dict[str, typing.Any]:
        """降级输入：直接使用 adb shell input text。返回统一底层结果。"""

        def escape(s: str) -> str:
            """
            adb shell input text 转义：
            - 空格 => %s
            - 其它字符尽量做 URL 编码（多数 ROM 可用）
            """
            s = s.replace(" ", "%s")
            return urllib.parse.quote(s, safe="%._-~:/@")

        if not text:
            return {"ok": True, "raw": ""}

        cmd = self.prefix + [
            "shell", "input", "text", escape(str(text))
        ]

        raw = await Terminal.cmd_line(cmd)
        out = raw or str(raw).strip().lower()

        ok = not any(k in out for k in ("error", "exception", "not found", "invalid"))

        return {"ok": ok, "raw": out}

    # workflow: ==== UI ====
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
        """滑动查找目标元素，返回基础数据，不包装 MCP 多模态结构。"""
        if by == "xpath":
            return {
                "ok"        : False,
                "found"     : False,
                "by"        : by,
                "value"     : value,
                "direction" : direction,
                "swipes"    : 0,
                "reason"    : "xpath_not_supported",
                "actions"   : []
            }

        deadline = time.monotonic() + float(timeout)
        actions: list[dict[str, typing.Any]] = []

        if widget := await self.find_widget(by, value, match, ignore_case):
            return {
                "ok"          : True,
                "found"       : True,
                "by"          : by,
                "value"       : value,
                "match"       : match,
                "ignore_case" : ignore_case,
                "direction"   : direction,
                "swipes"      : 0,
                "widget"      : widget,
                "reason"      : "found_initial",
                "actions"     : actions
            }

        if not (wm := await self.st_wm_size()):
            return {
                "ok"          : False,
                "found"       : False,
                "by"          : by,
                "value"       : value,
                "match"       : match,
                "ignore_case" : ignore_case,
                "reason"      : "wm_size_unavailable",
                "actions"     : actions
            }

        w, h = wm
        if anchor is None:
            ax = int(w * 0.5)
            ay = int(h * 0.55)
        else:
            ax, ay = anchor

        stable_hits = 0
        last_sim: typing.Optional[float] = None

        with tempfile.TemporaryDirectory(prefix="scroll_until_caps_") as tmp:
            tmp_dir = Path(tmp)
            prev_path = str(tmp_dir / "prev.png")
            cur_path = str(tmp_dir / "cur.png")

            prev_ok = False
            if stop_on_stable:
                try:
                    prev_path = await self.screenshot(prev_path)
                    prev_ok = True
                except Exception as e:
                    actions.append({
                        "kind"  : "screenshot_prev_fail",
                        "error" : f"{type(e).__name__}: {e}"
                    })

            for i in range(1, int(max_swipes) + 1):
                if time.monotonic() >= deadline:
                    return {
                        "ok"              : False,
                        "found"           : False,
                        "by"              : by,
                        "value"           : value,
                        "match"           : match,
                        "ignore_case"     : ignore_case,
                        "direction"       : direction,
                        "swipes"          : i - 1,
                        "reason"          : "timeout",
                        "actions"         : actions,
                        "last_similarity" : round(last_sim, 4) if last_sim is not None else None
                    }

                try:
                    await self.scroll_direction(direction, ax, ay, duration=duration)
                    actions.append({
                        "kind"      : "scroll",
                        "n"         : i,
                        "direction" : direction,
                        "anchor"    : [ax, ay],
                        "duration"  : duration
                    })
                except Exception as e:
                    actions.append({
                        "kind"  : "scroll_fail",
                        "n"     : i,
                        "error" : f"{type(e).__name__}: {e}"
                    })
                    return {
                        "ok"          : False,
                        "found"       : False,
                        "by"          : by,
                        "value"       : value,
                        "match"       : match,
                        "ignore_case" : ignore_case,
                        "direction"   : direction,
                        "swipes"      : i - 1,
                        "reason"      : "scroll_fail",
                        "actions"     : actions
                    }

                await asyncio.sleep(float(settle))

                if widget := await self.find_widget(by, value, match, ignore_case):
                    return {
                        "ok"          : True,
                        "found"       : True,
                        "by"          : by,
                        "value"       : value,
                        "match"       : match,
                        "ignore_case" : ignore_case,
                        "direction"   : direction,
                        "swipes"      : i,
                        "widget"      : widget,
                        "reason"      : "found_after_scroll",
                        "actions"     : actions
                    }

                if stop_on_stable and prev_ok:
                    try:
                        cur_path = await self.screenshot(cur_path)
                        sim = float(similarity(prev_path, cur_path))
                        last_sim = sim

                        stable_hits = stable_hits + 1 if sim >= float(similarity_threshold) else 0
                        actions.append({
                            "kind"        : "similarity",
                            "n"           : i,
                            "sim"         : round(sim, 4),
                            "stable_hits" : stable_hits
                        })

                        if i >= int(min_swipes_before_stop) and stable_hits >= int(stable_required):
                            return {
                                "ok"          : False,
                                "found"       : False,
                                "by"          : by,
                                "value"       : value,
                                "match"       : match,
                                "ignore_case" : ignore_case,
                                "direction"   : direction,
                                "swipes"      : i,
                                "reason"      : "stable_stop",
                                "similarity"  : round(sim, 4),
                                "stable_hits" : stable_hits,
                                "actions"     : actions
                            }

                        prev_path, cur_path = cur_path, prev_path
                    except Exception as e:
                        actions.append({
                            "kind"  : "similarity_fail",
                            "n"     : i,
                            "error" : f"{type(e).__name__}: {e}"
                        })

        return {
            "ok"              : False,
            "found"           : False,
            "by"              : by,
            "value"           : value,
            "match"           : match,
            "ignore_case"     : ignore_case,
            "direction"       : direction,
            "swipes"          : int(max_swipes),
            "reason"          : "max_swipes_reached",
            "last_similarity" : round(last_sim, 4) if last_sim is not None else None,
            "actions"         : actions
        }


if __name__ == '__main__':
    pass
