# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import math
import typing

from rich.console import Console
from rich.live import Live
from rich.text import Text


async def download_animation(
    *,
    console: Console,
    state: dict[str, typing.Any],
    stop_event: asyncio.Event
) -> None:
    """下载动画"""

    def fmt_size(num: float) -> str:
        unit = ["B", "KB", "MB", "GB", "TB"]
        idx = 0
        n = float(max(0.0, num))

        while n >= 1024.0 and idx < len(unit) - 1:
            n /= 1024.0
            idx += 1

        if n >= 100:
            return f"{n:>3.0f}{unit[idx]:<2}"
        if n >= 10:
            return f"{n:>4.1f}{unit[idx]:<2}"
        return f"{n:>4.2f}{unit[idx]:<2}"

    def clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def fmt_percent(phase: float, total: int) -> str:
        if total <= 0:
            return "--.-%"
        return f"{clamp01(phase) * 100:5.1f}%"

    def fmt_transfer(done: int, total: int, speed: float) -> str:
        if total > 0:
            size = f"{fmt_size(done).strip()}/{fmt_size(total).strip()}"
        else:
            size = fmt_size(done).strip()

        if speed > 0:
            return f"{size} · {fmt_size(speed).strip()}/s"
        return size

    def progress_bar(*, phase: float, total: int, tick: int, bar_width: int) -> str:
        cursor = int((bar_width - 1) * (0.5 + 0.5 * math.sin(tick * 0.22)))
        fill = int(bar_width * clamp01(phase)) if total > 0 else 0

        chars: list[str] = []
        for i in range(bar_width):
            if i < fill:
                chars.append("█")
            elif i == cursor:
                chars.append("▓")
            elif abs(i - cursor) == 1:
                chars.append("▒")
            else:
                chars.append("·")

        return "".join(chars)

    def busy_bar(*, tick: int, bar_width: int) -> str:
        cursor = tick % (bar_width * 2 - 2)
        if cursor >= bar_width:
            cursor = bar_width * 2 - 2 - cursor

        chars: list[str] = []
        for i in range(bar_width):
            if i == cursor:
                chars.append("▓")
            elif abs(i - cursor) == 1:
                chars.append("▒")
            else:
                chars.append("█")

        return "".join(chars)

    def stage_status(stage: str, *, phase: float, total: int) -> str:
        if stage == "connecting":
            return "package"
        if stage in {"warming", "downloading"}:
            return fmt_percent(phase, total)
        if stage == "verifying":
            return "checking"
        if stage == "extracting":
            return "unpacking"
        if stage == "installing":
            return "applying"
        if stage == "cleaning":
            return "cleaning"

        return "working"

    def stage_detail(
        stage: str,
        *,
        phase: float,
        done: int,
        total: int,
        speed: float,
        tick: int,
        bar_width: int
    ) -> str:
        if stage == "connecting":
            return f"[{busy_bar(tick=tick, bar_width=bar_width)}]"
        if stage in {"warming", "downloading"}:
            bar = progress_bar(phase=phase, total=total, tick=tick, bar_width=bar_width)
            return f"[{bar}] {fmt_transfer(done, total, speed)}"

        bar = busy_bar(tick=tick, bar_width=bar_width)
        return f"[{bar}] {fmt_transfer(done, total, 0.0)}"

    def final_frame() -> Text:
        """构建下载完成后的最终展示行。"""
        done = int(state.get("done") or 0)
        total = int(state.get("total") or 0)
        phase = float(state.get("phase") or 0.0)
        final_icon = str(state.get("final_icon") or "✓")
        final_label = str(state.get("final_label") or "complete")
        final_style = str(state.get("final_style") or "bold #87FFAF")

        out = Text()

        headline = f"{final_icon} {final_label}"
        detail = f" · {fmt_percent(phase, total)} · {fmt_transfer(done, total, 0.0)}"

        out.append(headline, style=final_style)
        out.append(detail, style="bold dim #8A8A8A")

        if (plain_len := len(out.plain)) < line_width:
            out.append(" " * (line_width - plain_len), style="bold dim #8A8A8A")

        return out

    async def frames() -> typing.AsyncGenerator[Text, None]:
        tick = 0
        while not stop_event.is_set():
            tick += 1

            stage = str(state.get("stage") or "warming")
            phase = float(state.get("phase") or 0.0)
            done = int(state.get("done") or 0)
            total = int(state.get("total") or 0)
            speed = float(state.get("speed") or 0.0)

            bar_width = 28
            line1 = f"{spin[tick % len(spin)]} {stage} · {stage_status(stage, phase=phase, total=total)}"
            line2 = stage_detail(
                stage, phase=phase, done=done, total=total, speed=speed, tick=tick, bar_width=bar_width
            )

            out = Text()
            out.append(line1[:line_width].ljust(line_width), style="bold #AFFFFF")
            if stage != "connecting":
                out.append("\n")
                out.append(line2[:line_width].ljust(line_width), style="bold dim #8A8A8A")

            yield out
            await asyncio.sleep(1 / 18)

        if state.get("stage") != "done" or state.get("final_label") != "complete":
            return

        for _ in range(3):
            yield final_frame()
            await asyncio.sleep(0.06)

    line_width = 56
    spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    agen = frames()

    first = await agen.__anext__()
    with Live(first, console=console, refresh_per_second=18, transient=True) as live:
        async for frame in agen:
            live.update(frame)

    if state.get("stage") == "done" and state.get("final_label") == "complete":
        console.print(final_frame())
        console.print()


if __name__ == '__main__':
    pass
