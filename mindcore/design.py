#  ____            _
# |  _ \  ___  ___(_) __ _ _ __
# | | | |/ _ \/ __| |/ _` | '_ \
# | |_| |  __/\__ \ | (_| | | | |
# |____/ \___||___/_|\__, |_| |_|
#                    |___/
#

import math
import random
import typing
import asyncio
import textwrap
from pathlib import Path
from rich.live import Live
from rich.text import Text
from rich.tree import Tree
from rich.console import Console
from rich.cells import cell_len
from mindnova import const


class Design(object):
    """Design class."""

    console: typing.Optional["Console"] = Console()

    def __init__(self, design_level: str = const.SHOW_LEVEL):
        self.design_level = design_level

    class Particle(object):
        """Particle class."""

        __slots__ = ("x", "y", "tx", "ty")

        def __init__(self, x: float, y: float, tx: int, ty: int):
            self.x = x
            self.y = y
            self.tx = tx
            self.ty = ty

    class Doc(object):
        """Doc class."""

        @classmethod
        def log(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"[bold]{text}")

        @classmethod
        def suc(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.SUC}{text}")

        @classmethod
        def wrn(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.WRN}{text}")

        @classmethod
        def err(cls, text: typing.Any) -> None:
            Design.console.print(const.PRINT_HEAD, f"{const.ERR}{text}")

    @staticmethod
    def startup_logo() -> None:
        color = random.choice([
            "#7C3AED",
            "#A855F7",
            "#6366F1",
            "#3B82F6",
            "#22D3EE",
            "#06B6D4",
            "#10B981",
            "#2DD4BF",
            "#F472B6",
            "#FB7185",
            "#FBBF24",
            "#A3E635",
            "#7C3AED",
            "#A855F7",
            "#6366F1",
            "#3B82F6",
            "#60A5FA",
            "#22D3EE",
            "#06B6D4",
            "#2DD4BF",
            "#10B981",
            "#34D399",
            "#0EA5E9",
            "#38BDF8",
        ])

        banner_standard = textwrap.dedent(f"""\
             __  __ _           _
            |  \/  (_)_ __   __| |
            | |\/| | | '_ \ / _` |
            | |  | | | | | | (_| |
            |_|  |_|_|_| |_|\__,_|
        """)
        banner_speed = textwrap.dedent(f"""\
            ______  _______       _________
            ___   |/  /__(_)____________  /
            __  /|_/ /__  /__  __ \  __  /
            _  /  / / _  / _  / / / /_/ /
            /_/  /_/  /_/  /_/ /_/\__,_/
        """)
        banner = random.choice([banner_standard, banner_speed])

        Design.console.print(f"[bold {color}]{banner}")
        Design.console.print(const.DECLARE)

    @staticmethod
    def show_done() -> None:
        task_done = textwrap.dedent(f"""\
            [bold #00FF88]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Done             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_done)

    @staticmethod
    def show_exit() -> None:
        task_exit = textwrap.dedent(f"""\
            [bold #FFEE55]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Exit             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_exit)

    @staticmethod
    def show_fail() -> None:
        task_fail = textwrap.dedent(f"""\
            [bold #FF4444]
            ╭────────────────────────────────────────╮
            │             {const.APP_DESC} Task Fail             │
            ╰────────────────────────────────────────╯
        """)
        Design.console.print(task_fail)

    @staticmethod
    async def typewriter(
        live: Live,
        delta: str,
        out: str,
        begin_delay: float,
        final_delay: float,
        *,
        cursor: str = "█"
    ) -> tuple[str, float]:

        pause: float         = 0.06
        jitter: float        = 0.0025
        breathe_every: int   = 80
        breathe_pause: float = 0.18
        glitch: float        = 0.01
        flush_chars: int     = 3
        flush_ms: float      = 0.02

        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

        n, pending, last_flush = max(len(delta), 1), 0, loop.time()

        render: typing.Callable[
            [str], None
        ] = lambda x: live.update(Text(x + cursor, style="bold #EEEEEE"))

        for i, ch in enumerate(delta):
            # 线性加速 + 抖动
            d = begin_delay + (final_delay - begin_delay) * (i / (n - 1 if n > 1 else 1))
            d = max(0.0, d + random.uniform(-jitter, jitter))

            # 标点分级停顿
            if ch in "，,": d += pause * 0.6
            elif ch in "。.!！?？": d += pause * 1.4
            elif ch in "；;：:": d += pause

            # 偶发 glitch
            if glitch and ch not in "\n\r\t" and ch.strip() and random.random() < glitch:
                live.update(Text(out + random.choice("▓▒░") + cursor))
                await asyncio.sleep(0.010); render(out)

            out += ch; pending += 1

            now = loop.time()
            if pending >= flush_chars or (now - last_flush) >= flush_ms:
                render(out); last_flush = now; pending = 0

            if breathe_every and (len(out) % breathe_every == 0):
                d += breathe_pause
            await asyncio.sleep(d)

        if pending: render(out)

        return out, final_delay

    @staticmethod
    async def cursor_blink(
        live: Live,
        out: str,
        *,
        cursor: str = "█"
    ) -> None:

        shades = ["#CFCFCF", "#E6E6E6", "#D8D8D8"]

        for _ in range(2):
            live.update(Text(out + cursor, style=f"bold {shades[1]}"))
            await asyncio.sleep(0.08)

            live.update(Text(out + " ", style=f"bold {shades[0]}"))
            await asyncio.sleep(0.06)

        live.update(Text(out, style=f"bold {shades[2]}"))
        await asyncio.sleep(0.05)

        live.update(Text(out, style="bold #C6C6C6"))

    @staticmethod
    def build_file_tree(file_path: str) -> None:
        """
        显示树状图。
        """
        color_schemes = {
            "Ocean Breeze": ["#AFD7FF", "#87D7FF", "#5FAFD7"],  # 根 / 中间 / 文件
            "Forest Pulse": ["#A8FFB0", "#87D75F", "#5FAF5F"],
            "Neon Sunset": ["#FFAF87", "#FF875F", "#D75F5F"],
            "Midnight Ice": ["#C6D7FF", "#AFAFD7", "#8787AF"],
            "Cyber Mint": ["#AFFFFF", "#87FFFF", "#5FD7D7"]
        }
        file_icons = {
            "folder": "📁",
            ".json": "📦",
            ".yaml": "🧾",
            ".yml": "🧾",
            ".md": "📝",
            ".log": "📄",
            ".html": "🌐",
            ".sh": "🔧",
            ".bat": "🔧",
            ".db": "🗃️",
            ".sqlite": "🗃️",
            ".zip": "📦",
            ".tar": "📦",
            "default": "📄"
        }
        text_color = random.choice([
            "#8A8A8A", "#949494", "#9E9E9E", "#A8A8A8", "#B2B2B2"
        ])

        root_color, folder_color, file_color = random.choice(list(color_schemes.values()))

        choice_icon: typing.Callable[
            [typing.Union[Path, str]], str
        ] = lambda x: file_icons["folder"] if (y := Path(x)).is_dir() else (
            file_icons[n] if (n := y.name.lower()) in file_icons else file_icons["default"]
        )

        parts = Path(file_path).parts

        # 根节点
        root = parts[0]
        tree = Tree(
            f"[bold {text_color}]{choice_icon(root)} {root}[/]", guide_style=f"bold {root_color}"
        )
        current_path = parts[0]
        current_node = tree

        # 处理中间的文件夹
        for part in parts[1:-1]:
            current_path = Path(current_path, part)
            current_node = current_node.add(
                f"[bold {text_color}]{choice_icon(current_path)} {part}[/]", guide_style=f"bold {folder_color}"
            )

        ext = (file := Path(parts[-1])).suffix.lower()
        current_node.add(f"[bold {file_color}]{choice_icon(ext)} {file.name}[/]")

        Design.console.print(tree)

    @staticmethod
    async def compile_animation(duration: float = 2, fps: int = 10) -> None:
        line_w = min(72, max(52, Design.console.width - 4))
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
        with Live(frame(0, 0.0), console=Design.console, refresh_per_second=fps) as live:
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

    @staticmethod
    async def particle_aggregate() -> None:

        def padding(char: str) -> str:
            return indent + char[:w].ljust(w)

        def compose(text: str, font_dict: dict[str, list[str]], gap: int = 2) -> list[str]:
            text  = text.upper()
            blank = [" " * 7] * 7
            parts = [font_dict.get(ch, blank) for ch in text]
            sep   = " " * gap
            return [sep.join(part[r] for part in parts) for r in range(7)]

        def backcloth() -> list[list[str]]:
            # 背景粒子：（中心稀疏、边缘更密）
            dots = ["·", "∙", "⋅", "•", "∘"]
            base = 0.06  # 基础密度（中心区域）
            edge = 0.06  # 边缘加密（越大越“包围感”）

            cx, cy = (w - 1) / 2, (h - 1) / 2
            max_d = (cx * cx + cy * cy) ** 0.5 or 1.0

            backcloth_list: list[list[str]] = []
            for y_ in range(h):
                row: list[str] = []
                for x_ in range(w):
                    d_x, d_y = x_ - cx, y_ - cy
                    d = ((d_x * d_x + d_y * d_y) ** 0.5) / max_d  # 0=中心, 1=边缘
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

        # (settled, moving, boot_line, ready_line, bg)  —— 每套同色系
        palettes = [
            # Cyan 系（品牌感强）
            ("bold #5FFFFF", "bold #87FFFF", "dim  #3FBDBD", "bold #5FFFFF", "bold #102A2A"),  # cyan neon
            ("bold #00D7FF", "bold #5FFFFF", "dim  #2FA9BF", "bold #00D7FF", "bold #0F2328"),  # deep cyan

            # Aqua / Teal 系（更冷更科技）
            ("bold #5FFFD7", "bold #87FFE9", "dim  #3FBFAE", "bold #5FFFD7", "bold #102622"),  # aqua mint
            ("bold #00FFAF", "bold #5FFFC7", "dim  #35BFA0", "bold #00FFAF", "bold #10261F"),  # teal green

            # Green 系（偏“完成/OK”）
            ("bold #87FF00", "bold #AFFF5F", "dim  #6BBF2A", "bold #87FF00", "bold #17230F"),  # lime
            ("bold #5FFF87", "bold #87FFAF", "dim  #4ABF73", "bold #5FFF87", "bold #112016"),  # spring green

            # Yellow / Gold 系（偏暖但仍统一）
            ("bold #FFD75F", "bold #FFEA87", "dim  #BFA24A", "bold #FFD75F", "bold #241F12"),  # warm gold
            ("bold #FFAF5F", "bold #FFC287", "dim  #BF824A", "bold #FFAF5F", "bold #241A12"),  # amber

            # Purple 系（霓虹紫科技）
            ("bold #AF87FF", "bold #C7AFFF", "dim  #8A66CF", "bold #AF87FF", "bold #1C1426"),  # violet
            ("bold #D787FF", "bold #E7B7FF", "dim  #A066CF", "bold #D787FF", "bold #201126"),  # magenta purple

            # Pink 系（少量跳色也可，但主题内仍统一）
            ("bold #FF5FAF", "bold #FF87C7", "dim  #C74F8A", "bold #FF5FAF", "bold #24121C"),  # neon pink
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

        # 背景粒子：（中心稀疏、边缘更密）
        background = backcloth()

        particles: list[Design.Particle] = []
        for (tx, ty) in targets:
            if (side := rng.randrange(4)) == 0:
                # top
                x, y = rng.uniform(0, w - 1), -rng.uniform(1, h)
            elif side == 1:
                # bottom
                x, y = rng.uniform(0, w - 1), h - 1 + rng.uniform(1, h)
            elif side == 2:
                # left
                x, y = -rng.uniform(1, w), rng.uniform(0, h - 1)
            else:
                # right
                x, y = w - 1 + rng.uniform(1, w), rng.uniform(0, h - 1)
            particles.append(Design.Particle(x=x, y=y, tx=tx, ty=ty))

        ticks      = max(1, int(duration * fps))
        slogan     = f"Agent ready • launch"
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

                if y_loc != h - 1: out.append("\n")

            out.append("\n")
            if final:
                out.append(final_line, style=style_final)
            else:
                out.append(begin_line, style=style_begin)

            return out

        with Live(render(0), console=Design.console, refresh_per_second=fps) as live:
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

    @staticmethod
    async def prefix_line(stop_event: asyncio.Event) -> None:
        """一行纯动画：无文字；宽度固定 30；增强闪断/撕裂/回弹；stop.set() 停止并触发收束。"""
        width: int = 30
        fps: int   = 60
        dust: str  = "·∙•"
        flash: str = "▓▒░"

        t = 0

        async def draw(chars: list[str], dt: float = 0.0) -> None:
            live.update(Text("".join(chars[:width]).ljust(width), style="bold"))
            if dt > 0: await asyncio.sleep(dt)

        def base_frame(tick: int) -> list[str]:
            line = [random.choice(dust) if random.random() < 0.10 else " " for _ in range(width)]

            x = tick % (2 * (width - 1))
            x = (2 * (width - 1) - x) if x >= (width - 1) else x

            line[x] = "█"

            if 0 <= x - 1 < width and random.random() < 0.85: line[x - 1] = "▉"
            if 0 <= x + 1 < width and random.random() < 0.85: line[x + 1] = "▊"

            return line

        def tear(line: list[str]) -> tuple[list[str], list[str]]:
            a = random.randrange(0, width - 8)
            b = random.randrange(a + 4, min(width, a + random.randint(10, 18)))

            shift = random.choice([-4, -3, -2, 2, 3, 4])

            out = line[:]
            seg = out[a:b]
            for i in range(a, b):
                j = i + shift
                if 0 <= j < width:
                    out[j] = seg[i - a]

            for i in range(a, b):
                if random.random() < 0.28:
                    out[i] = random.choice(flash)

            rebound = line[:]
            for i in range(a, b):
                if random.random() < 0.18:
                    rebound[i] = random.choice(flash)
            return out, rebound

        def blackout() -> list[str]:
            return [
                random.choice(flash) if random.random() < 0.55 else " "
                for _ in range(width)
            ]

        def afterglow(line: list[str]) -> list[str]:
            out = line[:]

            for _ in range(random.randint(2, 6)):
                k = random.randrange(0, width)
                out[k] = random.choice(flash)

            return out

        async def render(line: list[str], *, glitch_p: float = 0.22) -> None:
            if (r := random.random()) < glitch_p * 0.55:
                await draw(blackout(), 0.012)
                await draw(blackout(), 0.010)
                await draw(afterglow(line), 0.014)
                return None

            if r < glitch_p:
                torn, rebound = tear(line)
                await draw(torn, 0.014)
                await draw(blackout(), 0.010)
                await draw(rebound, 0.014)
                await draw(afterglow(line), 0.010)
                return None

            await draw(line)

        async def settle(final_tick: int, *, steps: int = 10) -> None:
            """收束：glitch 逐步熄灭 -> 噪点收拢到中心 -> 一次轻闪 -> 清空一行。"""
            mid = width // 2
            for s in range(steps):
                line = base_frame(final_tick + s)

                # glitch 概率逐步下降
                gp = 0.18 * (1.0 - (s / max(steps - 1, 1)))

                # 把噪点“吸向中心”（越往后吸得越狠）
                pull, out = (s + 1) / steps, [" "] * width

                for i, ch in enumerate(line):
                    if ch.strip():
                        j = int(i + (mid - i) * pull)
                        j = max(0, min(width - 1, j))
                        out[j] = ch

                # 光标也收拢到中心，最后变成一个点
                out[mid] = "█" if s < steps - 1 else "▉"

                await render(out, glitch_p=gp)
                await asyncio.sleep(0.010 + 0.010 * (s / steps))

            # 轻闪一下（收尾“咔哒”）
            await draw(blackout(), 0.014)
            await draw([" "] * width, 0.010)

        with Live(Text(" " * width), console=Design.console, refresh_per_second=fps) as live:
            while not stop_event.is_set():
                t += 1
                frame = base_frame(t)
                await render(frame)
                await asyncio.sleep(1 / fps)

            await settle(t)
            await draw([" "] * width)

    async def deep_thinking(self, task_info: list, task_event: asyncio.Event) -> None:
        if self.design_level != const.SHOW_LEVEL:
            return None

        indent  = "  "
        line_w  = min(68, max(44, self.console.width - 10))
        inner_w = line_w - cell_len(indent)

        spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        logo = const.APP_DESC

        dur = 1.0
        fps = 60
        rng = random.Random(7)

        steps = max(12, int(dur * fps))

        def padding(out: Text) -> Text:
            out.truncate(line_w, overflow="crop")
            if (remain := line_w - cell_len(out.plain)) > 0:
                out.append(" " * remain)
            return out

        def shimmer_logo(t: int) -> Text:
            pos = t % max(1, len(logo) + 6)

            out = Text()
            out.append(indent)
            out.append("⌁ ", style="bold #444444")
            for i, ch in enumerate(logo):
                if (d := abs((i + 2) - pos)) == 0:
                    out.append(ch, style="bold #87FFFF")
                elif d == 1:
                    out.append(ch, style="bold #AFFFFF")
                elif d == 2:
                    out.append(ch, style="bold #5FFFFF")
                else:
                    out.append(ch, style="bold #BBBBBB")

            out.append("  ", style="bold")
            return padding(out)

        def scan_line(t: int, p: float) -> Text:
            out  = Text(indent)
            bar  = min(28, max(18, line_w - 24))
            scan = int((bar - 1) * (0.5 + 0.5 * math.sin(p * math.tau)))

            out.append(f"{spin[t % len(spin)]} ", style="bold #AFFFFF")
            out.append("starting ", style="bold")
            out.append("[", style="bold #666666")

            for i in range(bar):
                if i == scan:
                    out.append("█", style="bold #87FFFF")
                elif abs(i - scan) == 1:
                    out.append("█", style="bold #5FFFFF")
                else:
                    out.append("·", style="#2A2A2A")

            out.append("]", style="bold #666666")
            out.append(" ", style="bold")

            right = "warming…".ljust(10)
            out.append(right, style="bold dim")

            if (pulse := (t % 6)) in (0, 1):
                out.append(" ▋", style="bold #87FFFF")
            elif pulse in (2, 3):
                out.append(" ▋", style="bold #5FFFFF")
            else:
                out.append(" ▋", style="bold #2A2A2A")

            return padding(out)

        def micro_glitch(t: int) -> Text:
            phases = ["warming", "priming", "binding", "syncing"]
            phase  = phases[(t // 10) % len(phases)]
            base   = f"boot: {phase} • cache warm • tools online"

            s = base.ljust(inner_w)
            if t % 13 in (0, 1):
                s_list = list(s)
                for _ in range(3):
                    idx = rng.randrange(0, len(s_list))
                    s_list[idx] = rng.choice("≈≋∿-_/\\")
                s = "".join(s_list)
                out = Text(indent + s, style="dim #8A8A8A")
            else:
                out = Text(indent + s, style="dim #777777")

            return padding(out)

        def spark_bits(t: int, p: float) -> Text:
            w = line_w - len(indent)

            rng2 = random.Random(1000 + t)
            hot1 = int((w - 1) * (0.5 + 0.5 * math.cos(p * math.tau)))
            hot2 = int((w - 1) * (0.5 + 0.5 * math.sin((p * 0.85 + 0.17) * math.tau)))

            out = Text(indent)
            for i in range(w):
                if i == hot1:
                    out.append("*", style="bold #87FFFF")
                elif abs(i - hot1) == 1:
                    out.append("+", style="bold #5FFFFF")
                elif i == hot2:
                    out.append("•", style="dim #6BDDDD")
                elif abs(i - hot2) == 1:
                    out.append("·", style="dim #3A3A3A")
                else:
                    out.append(rng2.choice("01  "), style="bold #2A2A2A")

            return padding(out)

        def build_task_info() -> Text:
            t = Text()
            for info in task_info:
                row = Text(f"{indent}{info}", style="bold #FFAF87")
                row.truncate(line_w, overflow="ellipsis")
                t.append_text(row)
                t.append("\n")
            return t

        def finished() -> None:
            final = Text()
            final.append("\n")
            final.append(indent + f"✓ {const.APP_DESC} Ready\n", style="bold #87FF00")
            final.append_text(build_task_info())
            final.append("\n")
            self.console.print(final)

        def frame(t: int, p: float) -> Text:
            txt = Text()
            txt.append("\n")
            txt.append_text(shimmer_logo(t))
            txt.append("\n")
            txt.append_text(scan_line(t, p))
            txt.append("\n")
            txt.append_text(micro_glitch(t))
            txt.append("\n")
            txt.append_text(spark_bits(t, p))
            txt.append("\n")
            txt.append_text(build_task_info())

            return txt

        with Live(frame(0, 0.0), console=self.console, refresh_per_second=fps, transient=True) as live:
            tick = 0
            while not task_event.is_set():
                for step in range(steps):
                    live.update(frame(tick, (step + 1) / steps))
                    tick += 1
                    await asyncio.sleep(1 / fps)

        finished()


if __name__ == '__main__':
    pass
