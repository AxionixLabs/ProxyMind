# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import random
import typing
import asyncio
from dataclasses import dataclass
from rich.live import Live
from rich.text import Text
from rich.console import Console
from mind_nova import const


@dataclass(slots=True)
class Particle:
    x: float
    y: float
    tx: int
    ty: int


async def compile_animation(
    *,
    console: Console,
    duration: float = 2,
    fps: int = 10
) -> None:
    line_w = min(72, max(52, console.width - 4))
    bar_w  = 28

    spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    bits = "01"
    rng  = random.Random(7)
    logo = const.APP_DESC

    def shimmer_logo(t: int) -> Text:
        pos = t % max(1, len(logo))
        out = Text()
        for i, ch in enumerate(logo):
            d = abs(i - pos)
            if d == 0:
                out.append(ch, style="bold #87FFFF")
            elif d == 1:
                out.append(ch, style="bold #AFFFFF")
            elif d == 2:
                out.append(ch, style="bold #5FFFFF")
            else:
                out.append(ch, style="bold #BBBBBB")
        return out

    def glitch_line(t: int) -> Text:
        core = f"boot sequence: stage {1 + (t // 8) % 3}/3"
        core = core.ljust(line_w)
        out  = Text()
        if t % 10 in (0, 1):
            s = list(core)
            for _ in range(3):
                idx = rng.randrange(0, min(line_w, len(s)))
                s[idx] = rng.choice("≈≋∿-_/\\")
            out.append("".join(s), style="dim #AAAAAA")
        else:
            out.append(core, style="dim #8A8A8A")
        return out

    def progress_bar(p: float, t: int) -> Text:
        filled = max(0, min(bar_w, int(bar_w * p)))
        scan   = int((bar_w - 1) * (0.5 + 0.5 * (1 if (t // 6) % 2 == 0 else -1)))

        out = Text()
        out.append(f"{spin[t % len(spin)]} ", style="bold #AFFFFF")

        out.append_text(shimmer_logo(t))
        out.append(" ", style="bold")

        out.append("[", style="bold #888888")
        for i in range(bar_w):
            if i < filled:
                if i == (filled - 1) or i == scan:
                    out.append("█", style="bold #87FFFF")
                else:
                    out.append("█", style="bold #5FFFFF")
            else:
                out.append("·", style="bold #3A3A3A")
        out.append("]", style="bold #888888")

        pct = f" {int(p * 100):>3d}%"
        out.append(pct, style="bold #87FFAF")

        if (plain_len := len(out.plain)) < line_w:
            out.append(" " * (line_w - plain_len))

        return out

    def micro_logs(t: int) -> Text:
        stage = (t // 10) % 3
        a = ["• preparing…", "• compiling…", "• packaging…"][stage].ljust(line_w)
        b = ["• warming cache…", "• linking…", "• sealing artifacts…"][stage].ljust(line_w)
        out = Text()
        out.append(a + "\n", style="bold dim")
        out.append(b, style="bold dim")
        return out

    def bit_rain(t: int) -> Text:
        rng2 = random.Random(t)
        s    = "".join(rng2.choice(bits + "  ") for _ in range(line_w))
        out  = Text()
        out.append(s, style="bold #2A2A2A")
        return out

    def frame(t: int, p: float) -> Text:
        txt = Text()
        txt.append_text(progress_bar(p, t))
        txt.append("\n")
        txt.append_text(glitch_line(t))
        txt.append("\n")
        txt.append_text(micro_logs(t))
        txt.append("\n")
        txt.append_text(bit_rain(t))
        return txt

    ticks = max(1, int(duration * fps))
    with Live(frame(0, 0.0), console=console, refresh_per_second=fps) as live:
        for tick in range(ticks):
            phase = (tick + 1) / ticks
            live.update(frame(tick, phase))
            await asyncio.sleep(1 / fps)

        done = Text()
        done.append("✓ build ready".ljust(line_w), style="bold #87FF00")
        done.append("\n")
        done.append("• prepared ✓".ljust(line_w), style="bold dim")
        done.append("\n")
        done.append("• compiled ✓".ljust(line_w), style="bold dim")
        done.append("\n")
        done.append("• packaged ✓".ljust(line_w), style="bold dim")
        done.append("\n")
        done.append((" " * line_w), style="bold #2A2A2A")
        live.update(done)
        await asyncio.sleep(0.25)


async def particle_aggregate(*, console: Console) -> None:

    def padding(char: str) -> str:
        return indent + char[:w].ljust(w)

    def compose(text: str, font_dict: dict[str, list[str]], gap: int = 2) -> list[str]:
        text  = text.upper()
        blank = [" " * 7] * 7
        parts = [font_dict.get(ch, blank) for ch in text]
        sep   = " " * gap
        return [sep.join(part[r] for part in parts) for r in range(7)]

    def backcloth() -> list[list[str]]:
        dots = ["·", "∙", "⋅", "•", "∘"]
        base = 0.06
        edge = 0.06

        cx, cy = (w - 1) / 2, (h - 1) / 2
        max_d = (cx * cx + cy * cy) ** 0.5 or 1.0

        backcloth_list: list[list[str]] = []
        for y_ in range(h):
            row: list[str] = []
            for x_ in range(w):
                d_x, d_y = x_ - cx, y_ - cy
                d = ((d_x * d_x + d_y * d_y) ** 0.5) / max_d
                p = base + edge * d
                row.append(rng.choice(dots) if rng.random() < p else " ")
            backcloth_list.append(row)

        return backcloth_list

    font = {
        "M": ["##   ##", "### ###", "## # ##", "##   ##", "##   ##", "##   ##", "##   ##"],
        "I": ["#######", "  ###  ", "  ###  ", "  ###  ", "  ###  ", "  ###  ", "#######"],
        "N": ["##   ##", "###  ##", "#### ##", "## ####", "##  ###", "##   ##", "##   ##"],
        "D": ["###### ", "##   ##", "##   ##", "##   ##", "##   ##", "##   ##", "###### "],
    }

    palettes = [
        ("bold #5FFFFF", "bold #87FFFF", "dim  #3FBDBD", "bold #5FFFFF", "bold #102A2A"),
        ("bold #00D7FF", "bold #5FFFFF", "dim  #2FA9BF", "bold #00D7FF", "bold #0F2328"),
        ("bold #5FFFD7", "bold #87FFE9", "dim  #3FBFAE", "bold #5FFFD7", "bold #102622"),
        ("bold #00FFAF", "bold #5FFFC7", "dim  #35BFA0", "bold #00FFAF", "bold #10261F"),
        ("bold #87FF00", "bold #AFFF5F", "dim  #6BBF2A", "bold #87FF00", "bold #17230F"),
        ("bold #5FFF87", "bold #87FFAF", "dim  #4ABF73", "bold #5FFF87", "bold #112016"),
        ("bold #FFD75F", "bold #FFEA87", "dim  #BFA24A", "bold #FFD75F", "bold #241F12"),
        ("bold #FFAF5F", "bold #FFC287", "dim  #BF824A", "bold #FFAF5F", "bold #241A12"),
        ("bold #AF87FF", "bold #C7AFFF", "dim  #8A66CF", "bold #AF87FF", "bold #1C1426"),
        ("bold #D787FF", "bold #E7B7FF", "dim  #A066CF", "bold #D787FF", "bold #201126"),
        ("bold #FF5FAF", "bold #FF87C7", "dim  #C74F8A", "bold #FF5FAF", "bold #24121C"),
    ]

    duration: float = 1.0
    fps: int        = 30
    indent: str     = "  "

    rng        = random.Random()
    candidates = [const.APP_NAME.upper()]
    word       = rng.choice(candidates)

    style_settled, style_moving, style_begin, style_final, style_bg = rng.choice(palettes)

    glyph = compose(word, font)

    gw, gh       = len(glyph[0]), len(glyph)
    pad_x, pad_y = 0, 1
    ox, oy       = pad_x, pad_y
    w, h         = gw + pad_x * 2, gh + pad_y * 2

    targets: list[tuple[int, int]] = [
        (ox + c, oy + r) for r in range(gh) for c in range(gw) if glyph[r][c] == "#"
    ]
    target_set = set(targets)

    background = backcloth()

    particles: list[Particle] = []
    for (tx, ty) in targets:
        if (side := rng.randrange(4)) == 0:
            x, y = rng.uniform(0, w - 1), -rng.uniform(1, h)
        elif side == 1:
            x, y = rng.uniform(0, w - 1), h - 1 + rng.uniform(1, h)
        elif side == 2:
            x, y = -rng.uniform(1, w), rng.uniform(0, h - 1)
        else:
            x, y = w - 1 + rng.uniform(1, w), rng.uniform(0, h - 1)
        particles.append(Particle(x=x, y=y, tx=tx, ty=ty))

    ticks      = max(1, int(duration * fps))
    slogan     = "Agent ready • launch"
    begin_line = padding(f"Booting… [{word}]")
    final_line = padding(f"✓ {slogan} [{word}]\n")

    def render(t: int, final: bool = False) -> Text:
        grid_state: dict[tuple[int, int], bool] = {}
        percent = min(1.0, (t + (1 if final else 0)) / ticks)

        for ptc in particles:
            if 0 <= (cx := int(round(ptc.x))) < w and 0 <= (cy := int(round(ptc.y))) < h:
                d = abs(ptc.x - ptc.tx) + abs(ptc.y - ptc.ty)
                settled = final or (percent > 0.85 and d < 0.9)

                if (cx, cy) not in grid_state:
                    grid_state[(cx, cy)] = settled
                else:
                    grid_state[(cx, cy)] = grid_state[(cx, cy)] or settled

        out: Text = Text()
        for y_loc in range(h):
            line_chars, line_styles = [], []
            for x_loc in range(w):
                if (key := (x_loc, y_loc)) in grid_state:
                    if grid_state[key]:
                        ch, style = "█", style_settled
                    else:
                        ch, style = "•", style_moving
                else:
                    if final and (x_loc, y_loc) in target_set:
                        ch, style = "█", style_settled
                    else:
                        ch, style = background[y_loc][x_loc], style_bg

                line_chars.append(ch)
                line_styles.append(style)

            out.append(indent)

            i = 0
            while i < w:
                j = i + 1
                while j < w and line_styles[j] == line_styles[i]:
                    j += 1
                out.append("".join(line_chars[i:j]), style=line_styles[i])
                i = j

            if y_loc != h - 1:
                out.append("\n")

        out.append("\n")
        if final:
            out.append(final_line, style=style_final)
        else:
            out.append(begin_line, style=style_begin)

        return out

    with Live(render(0), console=console, refresh_per_second=fps) as live:
        for tick in range(ticks):
            phase  = (tick + 1) / ticks
            ease   = 1 - (1 - phase) * (1 - phase)
            alpha  = 0.14 + 0.10 * ease
            jitter = 1.4 * (1 - ease)

            for particle in particles:
                dx = particle.tx - particle.x
                dy = particle.ty - particle.y
                particle.x += dx * alpha + (rng.random() - 0.5) * jitter * 0.18
                particle.y += dy * alpha + (rng.random() - 0.5) * jitter * 0.10

            live.update(render(tick))
            await asyncio.sleep(1 / fps)

        live.update(render(ticks, final=True))


async def download_animation(
    *,
    console: Console,
    state: dict[str, typing.Any],
    stop_event: asyncio.Event
) -> None:
    """下载动画"""

    def fmt_size(num: float) -> str:
        unit = ["B", "KB", "MB", "GB", "TB"]
        idx  = 0
        n    = float(max(0.0, num))

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
        fill   = int(bar_width * clamp01(phase)) if total > 0 else 0

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
        done         = int(state.get("done") or 0)
        total        = int(state.get("total") or 0)
        phase        = float(state.get("phase") or 0.0)
        final_icon   = str(state.get("final_icon") or "✓")
        final_label  = str(state.get("final_label") or "complete")
        final_style  = str(state.get("final_style") or "bold #87FFAF")

        out = Text()

        headline = f"{final_icon} {final_label}"
        detail   = f" · {fmt_percent(phase, total)} · {fmt_transfer(done, total, 0.0)}"

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
            done  = int(state.get("done") or 0)
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
    spin       = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    agen       = frames()

    first = await agen.__anext__()
    with Live(first, console=console, refresh_per_second=18, transient=True) as live:
        async for frame in agen:
            live.update(frame)

    if state.get("stage") == "done" and state.get("final_label") == "complete":
        console.print(final_frame())
        console.print()


if __name__ == '__main__':
    pass
