# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import random
import typing
import asyncio
import textwrap
from pathlib import Path
from collections import deque
from rich.live import Live
from rich.text import Text
from rich.tree import Tree
from rich.console import Console
from mind_nova import const


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
    def notify_update(local: dict[str, typing.Any], remote: dict[str, typing.Any]) -> None:
        Design.console.print(
            f"\n[bold]╭────── update available ──────╮\n"
            f"current:  [bold #AFFFFF]{local.get('version') or '-'}[/]\n"
            f"latest :  [bold #AFFFFF]{remote.get('version') or '-'}[/]\n"
            f"notes  :  [bold #8A8A8A]{remote.get('notes') or '-'}[/]\n"
        )

    @staticmethod
    async def typewriter(
        live: Live,
        delta: str,
        out: str,
        begin_delay: float,
        final_delay: float,
        cursor: str,
        *,
        max_lines: int | None = None,
        renderer: typing.Optional[typing.Callable[[str, str], Text]] = None
    ) -> tuple[str, float]:
        """
        - 默认：（整段 out 渲染）
        - 若 max_lines 指定：只显示末尾 max_lines 行（固定高度窗口）
        - renderer(text, cursor) 可自定义渲染（不传则使用默认 Text(text)+cursor）
        """

        pause: float         = 0.025   # 0.06 -> 0.025
        jitter: float        = 0.0012  # 0.0025 -> 0.0012
        breathe_every: int   = 160     # 80 -> 160（更少触发呼吸）
        breathe_pause: float = 0.06    # 0.18 -> 0.06
        glitch: float        = 0.003   # 0.01 -> 0.003（更少闪）
        flush_chars: int     = 2       # 3 -> 2（更跟手）
        flush_ms: float      = 0.012   # 0.02 -> 0.012

        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        n = max(len(delta), 1)
        pending, last_flush = 0, loop.time()

        def default_renderer(text: str, cur_cursor: str) -> Text:
            t = Text(text, style="bold", no_wrap=bool(max_lines), overflow="crop")
            t.append(cur_cursor, style="bold")
            return t

        render_fn = renderer or default_renderer

        class Window(object):

            __slots__ = ("lines", "cur")

            def __init__(self, max_len: int):
                self.lines: deque[str] = deque(maxlen=max_len)
                self.cur: str = ""

            def append_char(self, char: str) -> None:
                if char == "\n":
                    self.lines.append(self.cur)
                    self.cur = ""
                else:
                    self.cur += char

            def set_from_out_tail(self, full_out: str) -> None:
                parts = full_out.split("\n")  # 保留末尾是否换行的信息

                if full_out.endswith("\n"):
                    cur = ""
                    lines = parts[:-1]  # 最后一个是空串
                else:
                    cur = parts[-1] if parts else ""
                    lines = parts[:-1]

                self.lines.clear()
                for ln in lines[-self.lines.maxlen:]:
                    self.lines.append(ln)

                self.cur = cur

            def text(self, *, pad_to: typing.Optional[int] = None) -> str:
                rows = list(self.lines) + [self.cur]  # 始终带 cur 行（可为空串）
                if pad_to and pad_to > 0 and len(rows) < pad_to:
                    rows = [""] * (pad_to - len(rows)) + rows
                return "\n".join(rows)

        win: typing.Optional[Window] = Window(max_lines) if max_lines else None
        use_window = win is not None

        def render() -> None:
            text = win.text() if use_window else out
            live.update(render_fn(text, cursor))

        # 初始化窗口：如果 out 已有内容（跨多次 delta），只取末尾 max_lines 行
        if use_window and out:
            win.set_from_out_tail(out)
            render()

        for i, ch in enumerate(delta):
            # 线性加速 + 抖动
            denominator = (n - 1) if n > 1 else 1
            d = begin_delay + (final_delay - begin_delay) * (i / denominator)
            d = max(0.0, d + random.uniform(-jitter, jitter))

            # 标点分级停顿
            if ch in "，,": d += pause * 0.6
            elif ch in "。.!！?？": d += pause * 1.4
            elif ch in "；;：:": d += pause

            # 偶发 glitch
            if glitch and ch not in "\n\r\t" and ch.strip() and random.random() < glitch:
                if use_window:
                    live.update(
                        Text(win.text(), style="bold")
                        + Text(random.choice("▓▒░"), style="bold")
                        + Text(cursor, style="bold")
                    )
                    await asyncio.sleep(0.010)
                    render()
                else:
                    live.update(Text(out + random.choice("▓▒░") + cursor))
                    await asyncio.sleep(0.010)
                    render()

            # 追加字符（out 用于返回/持久化；窗口用于展示）
            out += ch
            if use_window:
                win.append_char(ch)

            pending += 1
            now = loop.time()
            if pending >= flush_chars or (now - last_flush) >= flush_ms:
                render()
                last_flush, pending = now, 0

            # breathe
            if breathe_every and (len(out) % breathe_every == 0):
                d += breathe_pause

            await asyncio.sleep(d)

        if pending:
            render()

        return out, final_delay

    @staticmethod
    async def cursor_blink(
        live: Live,
        out: str,
        cursor: str,
        *,
        max_lines: typing.Optional[int] = None,
        renderer: typing.Optional[typing.Callable[[str, bool], Text]] = None
    ) -> None:
        """
        - 默认：用全量 out
        - max_lines：只展示末尾 max_lines 行（与 typewriter 窗口一致）
        - renderer(text, cursor_on)：自定义渲染
        """

        def tail(text: str) -> str:
            if not max_lines: return text
            parts = text.splitlines()
            return "\n".join(parts[-max_lines:])

        def default_render(text: str, cursor_on: bool) -> Text:
            base = Text(text, style="bold", no_wrap=bool(max_lines), overflow="crop")
            if cursor_on:
                base += Text(cursor, style="reverse")
            return base

        r = renderer or default_render
        view = tail(out)

        for _ in range(2):
            live.update(r(view, True))
            await asyncio.sleep(0.08)
            live.update(r(view, False))
            await asyncio.sleep(0.06)

        live.update(r(view, False))

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
    async def download_animation(state: dict[str, typing.Any], stop_event: asyncio.Event) -> None:
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

        async def frames() -> typing.AsyncGenerator[Text, None]:
            tick = 0
            while not stop_event.is_set():
                tick += 1

                stage    = str(state.get("stage") or "warming")
                filename = str(state.get("filename") or "package")
                phase    = float(state.get("phase") or 0.0)
                done     = int(state.get("done") or 0)

                if phase <= 0.0:
                    phase = min(0.08, phase + 0.01)
                    state["phase"] = phase

                bar_w  = 24
                cursor = int((bar_w - 1) * (0.5 + 0.5 * math.sin(tick * 0.22)))
                fill   = int(bar_w * max(0.0, min(1.0, phase)))

                chars: list[str] = []
                for i in range(bar_w):
                    if i < fill: chars.append("█")
                    elif i == cursor: chars.append("▓")
                    elif abs(i - cursor) == 1: chars.append("▒")
                    else: chars.append("·")

                done_s = fmt_size(done)

                line1 = f"{spin[tick % len(spin)]} {stage}"
                line2 = filename
                line3 = f"[{' '.join(chars)}] {done_s}"

                out = Text()
                out.append(line1[:width].ljust(width), style="bold #AFFFFF")
                out.append("\n")
                out.append(line2[:width].ljust(width), style="bold dim #8A8A8A")
                out.append("\n")
                out.append(line3[:width].ljust(width), style="bold dim #8A8A8A")

                yield out
                await asyncio.sleep(1 / 18)

            for step in range(3):
                filename = str(state.get("filename") or "package")
                done_s   = fmt_size(float(state.get("done") or 0))

                out = Text()
                out.append("✓ complete".ljust(width), style="bold #87FFAF")
                out.append("\n")
                out.append(filename[:width].ljust(width), style="bold dim #8A8A8A")
                out.append("\n")
                out.append(f"size {done_s}".ljust(width), style="bold dim #8A8A8A")

                yield out
                await asyncio.sleep(0.06)

        width = 56
        spin  = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        agen  = frames()
        first = await agen.__anext__()

        with Live(first, console=Design.console, refresh_per_second=18) as live:
            async for frame in agen:
                live.update(frame)

    async def stream_wait_live(
        self,
        stop_event: asyncio.Event,
        theme: typing.Literal["chat", "fast", "plan"] = "chat"
    ) -> None:
        """流式等待动画效果。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        palettes: dict[str, dict[str, typing.Any]] = {
            "chat": {
                "glyphs": {
                    "spin"         : "◜◠◝◞◡◟",
                    "bubble_left"  : "〈《(",
                    "bubble_right" : ")》〉",
                    "bubble_dot"   : "●◉",
                    "focus"        : "◆",
                    "pulse"        : "•",
                    "echo"         : "·",
                    "beam_a"       : "═",
                    "beam_b"       : "─",
                    "noise"        : "˙",
                    "tail"         : "•",
                    "trail"        : "⋅",
                    "reply"        : "◦◎",
                    "listen"       : "◌◍",
                    "speak"        : "◉◍"
                },
                "colors": {
                    "prefix"     : "#A3E635",
                    "core"       : "#C4FFF0",
                    "near"       : "#9EF7E7",
                    "beam"       : "#67E8F9",
                    "beam_dim"   : "#3F9FB3",
                    "dust"       : "#3F3F46",
                    "sweep_core" : "#93C5FD",
                    "sweep_tail" : "#60A5FA",
                    "shell"      : "#244454",
                    "shell_dim"  : "#22313A",
                    "orbit_a"    : "#FDE68A",
                    "orbit_b"    : "#8BE9FD",
                    "orbit_c"    : "#5EEAD4"
                },
                "motion": {
                    "phase_div"      : 4.2,
                    "lead_freq"      : 1.08,
                    "reply_freq"     : 0.72,
                    "reply_phase"    : 1.45,
                    "breathe_freq"   : 0.88,
                    "sender_freq"    : 1.62,
                    "receiver_freq"  : 1.28,
                    "receiver_phase" : 2.2,
                    "chat_cycle"     : 16
                }
            },
            "fast": {
                "glyphs": {
                    "spin"   : "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
                    "packet" : "◈",
                    "core"   : "◆",
                    "near"   : "•",
                    "beam_a" : "=",
                    "beam_b" : "-",
                    "trail"  : ":",
                    "echo"   : "~",
                    "gate"   : ">",
                    "dust"   : "˙",
                    "orbit"  : ".",
                    "glitch" : "·:~"
                },
                "colors": {
                    "prefix"     : "#F59E0B",
                    "core"       : "#FFF3C4",
                    "near"       : "#FCD34D",
                    "beam"       : "#FB7185",
                    "beam_dim"   : "#BE5672",
                    "dust"       : "#4A2D33",
                    "sweep_core" : "#F97316",
                    "sweep_tail" : "#FB7185",
                    "shell"      : "#5B2C1A",
                    "shell_dim"  : "#3A2320",
                    "orbit_a"    : "#FDBA74",
                    "orbit_b"    : "#F472B6",
                    "orbit_c"    : "#FDE68A"
                },
                "motion": {
                    "phase_div"    : 3.0,
                    "lead_freq"    : 0.92,
                    "echo_phase"   : 0.85,
                    "pilot_freq"   : 1.8,
                    "pilot_offset" : 3.0,
                    "pilot_amp"    : 1.5
                }
            },
            "plan": {
                "glyphs": {
                    "spin"     : "◴◷◶◵",
                    "done"     : "◆",
                    "active"   : "◉",
                    "next"     : "◇",
                    "idle"     : "○",
                    "beam_a"   : "═",
                    "beam_b"   : "─",
                    "progress" : "▸",
                    "pulse"    : "•",
                    "echo"     : "·"
                },
                "colors": {
                    "prefix"     : "#34D399",
                    "core"       : "#D1FAE5",
                    "near"       : "#6EE7B7",
                    "beam"       : "#A7F3D0",
                    "beam_dim"   : "#4E9F8A",
                    "dust"       : "#31403D",
                    "sweep_core" : "#10B981",
                    "sweep_tail" : "#34D399",
                    "shell"      : "#1F4D45",
                    "shell_dim"  : "#203733",
                    "orbit_a"    : "#A7F3D0",
                    "orbit_b"    : "#93C5FD",
                    "orbit_c"    : "#C4B5FD"
                },
                "motion": {
                    "phase_div"   : 7.2,
                    "active_freq" : 0.8,
                    "bridge_freq" : 1.2
                }
            }
        }
        palette = palettes.get(theme, palettes["chat"])

        glyphs: dict[str, str] = palette["glyphs"]
        colors: dict[str, str] = palette["colors"]
        motion: dict[str, float] = palette["motion"]
        width = min(30, max(22, self.console.width - 22))

        fps  = 32
        spin = glyphs["spin"]
        tick = 0

        def clamp(pos: int) -> int:
            return max(0, min(width - 1, pos))

        def build_chat(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            cycle = max(12, int(motion["chat_cycle"]))
            beat  = i % cycle

            sender_talk     = beat in {1, 2, 3, 4, 5, 6, 7}
            sender_release  = beat in {8, 9}
            receiver_listen = beat in {10, 11, 12, 13}
            receiver_ack    = beat in {14, 15, 16, 17}

            left_rest      = 2
            right_rest     = width - 3
            sender_shift   = -1 if sender_talk else 0
            receiver_shift = 1 if receiver_ack else 0

            left       = clamp(left_rest + sender_shift)
            right      = clamp(right_rest + receiver_shift)
            lane_start = left + 2
            lane_end   = right - 2
            lane_span  = max(1, lane_end - lane_start)
            lead       = 0.5 + 0.5 * math.sin(phase * motion["lead_freq"])
            head_ratio = 0.10 + 0.78 * lead

            if sender_talk:
                head_ratio = min(0.96, head_ratio + 0.06)

            head       = clamp(lane_start + int(lane_span * head_ratio))
            tail_len   = min(8, max(4, width // 4))
            reply_gate = 0.5 + 0.5 * math.sin((phase * motion["reply_freq"]) + motion["reply_phase"])
            reply_head = clamp(lane_end - int((lane_span * 0.22) * reply_gate))
            chars      = [" "] * width

            styles: dict[int, str] = dict()

            left_shell       = glyphs["bubble_left"][1 if sender_talk else (2 if sender_release else 0)]
            right_shell      = glyphs["bubble_right"][1 if receiver_ack else (2 if receiver_listen else 0)]
            left_dot_idle    = glyphs["bubble_dot"][0]
            left_dot_talk    = glyphs["speak"][0] if (i % 4) < 2 else glyphs["speak"][1]
            right_dot_idle   = glyphs["listen"][0]
            right_dot_listen = glyphs["listen"][1] if (i % 4) < 2 else glyphs["pulse"]
            reply_glyph      = glyphs["reply"][1] if (i % 4) < 2 else glyphs["reply"][0]

            chars[left]  = left_shell
            styles[left] = (
                f"bold {colors['core']}" if sender_talk
                else (f"bold {colors['near']}" if sender_release else f"bold {colors['orbit_a']}")
            )

            left_dot = clamp(left + 1)
            chars[left_dot] = left_dot_talk if sender_talk else (glyphs["tail"] if sender_release else left_dot_idle)
            styles[left_dot] = (
                f"bold {colors['sweep_core']}" if sender_talk
                else (f"bold {colors['sweep_tail']}" if sender_release else f"bold {colors['core']}")
            )

            left_aura = clamp(left + 2)
            if sender_talk:
                chars[left_aura] = glyphs["tail"] if (i % 4) in {1, 2} else glyphs["trail"]
                styles[left_aura] = f"bold {colors['sweep_tail']}"
            elif sender_release and (i % 2 == 0):
                chars[left_aura] = glyphs["trail"]
                styles[left_aura] = f"bold {colors['beam_dim']}"

            chars[right]  = right_shell
            styles[right] = (
                f"bold {colors['orbit_b']}" if receiver_ack
                else (f"bold {colors['near']}" if receiver_listen else f"bold {colors['orbit_c']}")
            )

            right_dot = clamp(right - 1)
            chars[right_dot] = reply_glyph if receiver_ack else (right_dot_listen if receiver_listen else right_dot_idle)
            styles[right_dot] = (
                f"bold {colors['orbit_b']}" if receiver_ack
                else (f"bold {colors['core']}" if receiver_listen else f"bold {colors['near']}")
            )

            right_aura = clamp(right - 2)
            if receiver_ack:
                chars[right_aura] = glyphs["pulse"] if (i % 4) in {1, 2} else reply_glyph
                styles[right_aura] = f"bold {colors['orbit_b']}"
            elif receiver_listen and (i % 2 == 0):
                chars[right_aura] = glyphs["listen"][0]
                styles[right_aura] = f"bold {colors['beam_dim']}"

            for pos in range(lane_start, lane_end + 1):
                dist = head - pos
                reply_dist = abs(pos - reply_head)

                if pos == head:
                    chars[pos] = glyphs["focus"]
                    styles[pos] = f"bold {colors['sweep_core']}"
                elif 0 < dist <= 2:
                    chars[pos] = glyphs["tail"]
                    styles[pos] = f"bold {colors['core']}"
                elif 0 < dist <= tail_len:
                    chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                    styles[pos] = f"bold {colors['beam']}"
                elif 0 < dist <= tail_len + 4:
                    if (pos + i) % 2 != 0:
                        continue
                    chars[pos] = glyphs["trail"]
                    styles[pos] = f"bold {colors['beam_dim']}"
                elif head < pos <= min(lane_end, head + 2):
                    chars[pos] = glyphs["echo"]
                    styles[pos] = f"bold {colors['sweep_tail']}"
                elif pos >= right - 5 and reply_dist == 0 and receiver_ack:
                    chars[pos] = reply_glyph
                    styles[pos] = f"bold {colors['orbit_b']}"
                elif pos >= right - 5 and reply_dist <= 1 and receiver_ack and (i + pos) % 2 == 0:
                    chars[pos] = glyphs["pulse"]
                    styles[pos] = f"bold {colors['near']}"
                elif pos >= right - 6 and receiver_listen and (pos + i) % 2 == 0:
                    chars[pos] = glyphs["listen"][0]
                    styles[pos] = f"bold {colors['beam_dim']}"
                elif pos >= right - 7 and (pos + i) % 3 == 0:
                    chars[pos] = glyphs["echo"]
                    styles[pos] = f"bold {colors['shell']}"
                elif pos <= left + 4 and sender_talk and (pos + i) % 2 == 0:
                    chars[pos] = glyphs["trail"] if (i + pos) % 4 == 0 else glyphs["pulse"]
                    styles[pos] = f"bold {colors['shell_dim']}"
                elif (pos + i) % 7 == 0:
                    chars[pos] = glyphs["noise"]
                    styles[pos] = f"bold {colors['dust']}"

            for pos in (clamp(left - 1), clamp(left - 2), clamp(right + 1), clamp(right + 2)):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["noise"]
                styles[pos] = f"bold {colors['shell_dim']}"

            if sender_talk:
                ember = clamp(left - 1)
                chars[ember] = glyphs["trail"]
                styles[ember] = f"bold {colors['beam_dim']}"
            elif sender_release:
                ember = clamp(left - 1)
                chars[ember] = glyphs["echo"]
                styles[ember] = f"bold {colors['shell_dim']}"

            if receiver_ack:
                wink = clamp(right + 1)
                chars[wink] = reply_glyph
                styles[wink] = f"bold {colors['orbit_b']}"
            elif receiver_listen:
                wink = clamp(right + 1)
                chars[wink] = glyphs["trail"]
                styles[wink] = f"bold {colors['shell_dim']}"

            mid = clamp((lane_start + lane_end) // 2)
            if not chars[mid].strip() and abs(mid - head) > 3 and (i // 2) % 2 == 0:
                chars[mid] = glyphs["trail"]
                styles[mid] = f"bold {colors['beam_dim']}"
            return chars, styles

        def build_fast(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            lead  = 0.5 + 0.5 * math.sin(phase * motion["lead_freq"])
            head  = clamp(int(lead * (width - 1)))
            echo  = clamp(
                int((0.5 + 0.5 * math.sin((phase * motion["lead_freq"]) + motion["echo_phase"])) * (width - 1))
            )
            pilot = clamp(min(
                width - 1,
                head + int(motion["pilot_offset"] + motion["pilot_amp"] * math.sin(phase * motion["pilot_freq"]))
            ))
            chars = [" "] * width
            styles: dict[int, str] = {}
            glitch = glyphs["glitch"]

            for pos in range(width):
                dist = abs(pos - head)
                if dist == 0:
                    chars[pos] = glyphs["packet"]
                    styles[pos] = f"bold {colors['sweep_core']}"
                elif dist <= 1:
                    chars[pos] = glyphs["core"]
                    styles[pos] = f"bold {colors['core']}"
                elif dist <= 2:
                    chars[pos] = glyphs["near"]
                    styles[pos] = f"bold {colors['near']}"
                elif pos < head and (head - pos) <= 10:
                    tail = head - pos
                    if tail <= 2:
                        chars[pos] = glyphs["near"]
                        styles[pos] = f"bold {colors['near']}"
                    elif tail <= 5:
                        chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                        styles[pos] = f"bold {colors['beam']}"
                    else:
                        chars[pos] = glitch[(pos + i) % len(glitch)]
                        styles[pos] = f"bold {colors['beam_dim']}"
                elif pos > head and (pos - head) <= 2:
                    chars[pos] = glyphs["trail"]
                    styles[pos] = f"bold {colors['sweep_tail']}"
                elif pos < head and (head - pos) <= 14 and (pos + i) % 4 == 0:
                    chars[pos] = glitch[(head - pos + i) % len(glitch)]
                    styles[pos] = f"bold {colors['dust']}"
                elif pos > head and (pos - head) <= 6 and (pos + i) % 3 == 0:
                    chars[pos] = glyphs["dust"]
                    styles[pos] = f"bold {colors['shell_dim']}"
                elif (pos + i) % 9 == 0:
                    chars[pos] = glyphs["dust"]
                    styles[pos] = f"bold {colors['dust']}"

            if not chars[echo].strip():
                chars[echo] = glyphs["echo"]
                styles[echo] = f"bold {colors['beam_dim']}"

            if not chars[pilot].strip():
                chars[pilot] = glyphs["trail"]
                styles[pilot] = f"bold {colors['sweep_tail']}"

            for pos, color in (
                (clamp(head - 12), "orbit_a"),
                (clamp(head - 7), "orbit_b"),
                (clamp(head + 7), "orbit_c"),
            ):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["orbit"]
                styles[pos] = f"bold {colors[color]}"

            if head > 2:
                gate = clamp(head - 2)
                if not chars[gate].strip():
                    chars[gate] = glyphs["gate"]
                    styles[gate] = f"bold {colors['beam']}"

            return chars, styles

        def build_plan(i: int) -> tuple[list[str], dict[int, str]]:
            phase = i / motion["phase_div"]
            cols  = [2, width // 3, (2 * width) // 3, width - 3]
            chars = [" "] * width
            styles: dict[int, str] = {}
            active = int((0.5 + 0.5 * math.sin(phase * motion["active_freq"])) * (len(cols) - 1) + 0.5)
            bridge = 0.5 + 0.5 * math.sin(phase * motion["bridge_freq"])
            prev_idx = max(0, active - 1)
            next_idx = min(len(cols) - 1, active + 1)

            for idx, pos in enumerate(cols):
                if idx < active:
                    chars[pos] = glyphs["done"]
                    styles[pos] = f"bold {colors['beam']}"
                elif idx == active:
                    chars[pos] = glyphs["active"]
                    styles[pos] = f"bold {colors['core']}"
                elif idx == next_idx:
                    chars[pos] = glyphs["next"]
                    styles[pos] = f"bold {colors['near']}"
                else:
                    chars[pos] = glyphs["idle"]
                    styles[pos] = f"bold {colors['beam_dim']}"

            for idx in range(len(cols) - 1):
                a, b = cols[idx], cols[idx + 1]
                progress_pos = clamp(a + 1 + int((b - a - 2) * bridge))
                for pos in range(a + 1, b):
                    ratio = (pos - a) / max(b - a, 1)
                    if idx < active:
                        chars[pos] = glyphs["beam_a"] if (pos + i) % 2 == 0 else glyphs["beam_b"]
                        styles[pos] = f"bold {colors['beam']}"
                    elif idx == active and ratio <= bridge:
                        if pos == progress_pos:
                            chars[pos] = glyphs["progress"]
                            styles[pos] = f"bold {colors['sweep_core']}"
                        elif pos >= progress_pos - 2:
                            chars[pos] = glyphs["pulse"]
                            styles[pos] = f"bold {colors['sweep_tail']}"
                        else:
                            chars[pos] = glyphs["beam_b"]
                            styles[pos] = f"bold {colors['near']}"
                    elif idx == active:
                        chars[pos] = glyphs["echo"]
                        styles[pos] = f"bold {colors['shell']}"
                    elif (pos + i) % 6 == 0:
                        chars[pos] = glyphs["echo"]
                        styles[pos] = f"bold {colors['shell_dim']}"

            halo = [
                clamp(cols[active] - 2), clamp(cols[active] - 1),
                clamp(cols[active] + 1), clamp(cols[active] + 2)
            ]
            for idx, pos in enumerate(halo):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["echo"] if idx % 2 == 0 else glyphs["pulse"]
                styles[pos] = f"bold {(colors['orbit_a'], colors['orbit_b'], colors['orbit_c'], colors['orbit_a'])[idx]}"

            for pos in (clamp(cols[prev_idx] - 1), clamp(cols[next_idx] + 1)):
                if chars[pos].strip():
                    continue
                chars[pos] = glyphs["echo"]
                styles[pos] = f"bold {colors['orbit_b']}"
            return chars, styles

        def frame(i: int) -> Text:
            if theme == "fast":
                chars, styles = build_fast(i)
            elif theme == "plan":
                chars, styles = build_plan(i)
            else:
                chars, styles = build_chat(i)

            prefix = f"{spin[i % len(spin)]} "

            out = Text()
            out.append(prefix, style=f"bold {colors['prefix']}")

            for pos, ch in enumerate(chars):
                out.append(ch, style=styles.get(pos, "bold #2A2A2A"))
            return out

        async def settle(i: int) -> None:
            for step in range(7):
                fade = Text(" " * (width + 2), style="bold #202020")
                if step < 5:
                    live.update(frame(i + step))
                    await asyncio.sleep(0.016)
                live.update(fade)
                await asyncio.sleep(0.009)

        with Live(frame(0), console=self.console, refresh_per_second=fps, transient=True) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(frame(tick))
                await asyncio.sleep(1 / fps)

            await settle(tick)

    @classmethod
    async def preview_tool_status_live(cls, duration: float = 15.0) -> None:
        text     = "function calling"
        phase    = 0.0
        fps      = cls.tool_status_refresh_per_second()
        interval = cls.tool_status_interval()
        step     = cls.tool_status_step()

        deadline = asyncio.get_running_loop().time() + max(0.0, float(duration))

        with Live(
            cls.tool_status_renderable(phase, text),
            console=cls.console,
            refresh_per_second=fps,
            transient=True
        ) as live:
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(interval)
                phase += step
                live.update(cls.tool_status_renderable(phase, text))

    async def preview_search_status_live(cls, duration: float = 15.0) -> None:
        text     = "searching"
        phase    = 0.0
        fps      = 24
        interval = 1 / 24
        step     = 1.0
        deadline = asyncio.get_running_loop().time() + max(0.0, float(duration))

        with Live(
            cls.search_status_renderable(phase, text),
            console=cls.console,
            refresh_per_second=fps,
            transient=True
        ) as live:
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(interval)
                phase += step
                live.update(cls.search_status_renderable(phase, text))

    @classmethod
    def tool_status_renderable(cls, phase: float, text: str) -> Text:
        colors = {
            "edge"      : "bold #6A6256",
            "shell"     : "bold #C7B8A1",
            "core"      : "bold #FFF7E7",
            "pulse"     : "bold #F2DEC0",
            "dust"      : "bold #8F816E",
            "text_peak" : "bold #FFF7EA",
            "text_near" : "bold #EBD8B7",
            "text_mid"  : "bold #CDB999",
            "text_dim"  : "bold #9D8C79"
        }

        text  = str(text or "").strip() or "function calling"
        span  = max(1, len(text))
        sweep = ((math.sin(phase * 0.24) + 1.0) * 0.5) * max(1.0, span - 1)

        out = cls._tool_status_indicator(phase)
        out.append(" ", style=colors["edge"])

        for pos, char in enumerate(text):
            if char == " ":
                style = colors["text_dim"]
            else:
                distance = abs(pos - sweep)
                if distance < 0.55:
                    style = colors["text_peak"]
                elif distance < 1.4:
                    style = colors["text_near"]
                elif distance < 2.6:
                    style = colors["text_mid"]
                else:
                    style = colors["text_dim"]
            out.append(char, style=style)
        return out

    @classmethod
    def tool_status_static_renderable(cls, text: str) -> Text:
        colors = {
            "edge"      : "bold #6D655A",
            "text_peak" : "bold #F2E4C7",
            "text_mid"  : "bold #C4B093",
            "text_dim"  : "bold #978774"
        }

        text   = str(text or "").strip() or "function calling"
        center = max(0.0, (max(1, len(text)) - 1) / 2)
        out    = cls._tool_status_indicator(0.0, subtle=True)
        out.append(" ", style=colors["edge"])

        for pos, char in enumerate(text):
            if char == " ":
                style = colors["text_dim"]
            else:
                distance = abs(pos - center)
                if distance < 1.2:
                    style = colors["text_peak"]
                elif distance < 3.0:
                    style = colors["text_mid"]
                else:
                    style = colors["text_dim"]
            out.append(char, style=style)
        return out

    @classmethod
    def search_status_renderable(cls, phase: float, text: str) -> Text:
        stable_colors = {
            "edge"     : "bold #425663",
            "core"     : "bold #F0FBFF",
            "near"     : "bold #C8E2EC",
            "trail"    : "bold #87A8B6",
            "dust"     : "bold #526B77",
            "text"     : "bold #EAF8FC",
            "text_dim" : "bold #8EAAB5"
        }

        colors  = stable_colors
        breathe = 0.5 + (0.5 * math.sin(phase * 0.55))
        left_outer_phase = 0.5 + (0.5 * math.sin((phase * 0.55) - 1.45))
        left_inner_phase = 0.5 + (0.5 * math.sin((phase * 0.55) - 0.75))
        right_inner_phase = 0.5 + (0.5 * math.sin((phase * 0.55) + 0.75))
        right_outer_phase = 0.5 + (0.5 * math.sin((phase * 0.55) + 1.45))
        shell_phase = 0.5 + (0.5 * math.sin((phase * 0.55) + 2.1))
        drift   = int(phase * 0.65) % max(1, len(text))

        if breathe > 0.86:
            core = "*"
        elif breathe > 0.66:
            core = "O"
        elif breathe > 0.42:
            core = "o"
        else:
            core = "."

        if left_inner_phase > 0.82:
            left_inner = "o"
        elif left_inner_phase > 0.56:
            left_inner = "."
        else:
            left_inner = " "

        if right_inner_phase > 0.82:
            right_inner = "o"
        elif right_inner_phase > 0.56:
            right_inner = "."
        else:
            right_inner = " "

        if left_outer_phase > 0.88:
            left_outer = "o"
        elif left_outer_phase > 0.66:
            left_outer = "."
        else:
            left_outer = " "

        if right_outer_phase > 0.88:
            right_outer = "o"
        elif right_outer_phase > 0.66:
            right_outer = "."
        else:
            right_outer = " "

        edge_left = "<" if shell_phase > 0.5 else "["
        edge_right = ">" if shell_phase > 0.5 else "]"

        out = Text()

        out.append(edge_left, style=colors["edge"])
        out.append(left_outer, style=colors["trail"] if left_outer.strip() else colors["edge"])
        out.append(left_inner, style=colors["near"] if left_inner.strip() else colors["edge"])
        out.append(" ", style=colors["edge"])
        out.append(core, style=colors["core"])
        out.append(" ", style=colors["edge"])
        out.append(right_inner, style=colors["near"] if right_inner.strip() else colors["edge"])
        out.append(right_outer, style=colors["trail"] if right_outer.strip() else colors["edge"])
        out.append(edge_right, style=colors["edge"])

        out.append(" ", style=colors["edge"])
        for pos, char in enumerate(text):
            style = colors["text"] if pos == drift else colors["text_dim"]
            if char == " ":
                style = colors["text_dim"]
            out.append(char, style=style)
        return out

    @classmethod
    def thinking_status_renderable(cls, phase: float, text: str) -> Text:
        colors = {
            "edge"     : "bold #485E5C",
            "dot"      : "bold #E8F6F2",
            "dot_soft" : "bold #C6DED8",
            "dot_dim"  : "bold #7F9A94",
            "text"     : "bold #E6F1EE",
            "text_dim" : "bold #8EA6A0"
        }

        text = str(text or "").strip() or "thinking"
        breathe = 0.5 + (0.5 * math.sin(phase * 0.42))
        left_outer_phase = 0.5 + (0.5 * math.sin((phase * 0.42) - 1.7))
        left_inner_phase = 0.5 + (0.5 * math.sin((phase * 0.42) - 0.9))
        right_inner_phase = 0.5 + (0.5 * math.sin((phase * 0.42) + 0.9))
        right_outer_phase = 0.5 + (0.5 * math.sin((phase * 0.42) + 1.7))
        shell_phase = 0.5 + (0.5 * math.sin((phase * 0.42) + 2.2))

        out = Text()

        if left_outer_phase > 0.86:
            left_outer = "o"
        elif left_outer_phase > 0.64:
            left_outer = "."
        else:
            left_outer = " "

        if breathe > 0.82:
            core = "*"
        elif breathe > 0.62:
            core = "O"
        elif breathe > 0.42:
            core = "o"
        else:
            core = "."

        if left_inner_phase > 0.8:
            left_inner = "o"
        elif left_inner_phase > 0.56:
            left_inner = "."
        else:
            left_inner = " "

        if right_inner_phase > 0.8:
            right_inner = "o"
        elif right_inner_phase > 0.56:
            right_inner = "."
        else:
            right_inner = " "

        if right_outer_phase > 0.86:
            right_outer = "o"
        elif right_outer_phase > 0.64:
            right_outer = "."
        else:
            right_outer = " "

        edge_left = "<" if shell_phase > 0.58 else "["
        edge_right = ">" if shell_phase > 0.58 else "]"

        out.append(edge_left, style=colors["edge"])
        out.append(left_outer, style=colors["dot_dim"] if left_outer.strip() else colors["edge"])
        out.append(left_inner, style=colors["dot_soft"] if left_inner.strip() else colors["edge"])
        out.append(" ", style=colors["edge"])
        out.append(core, style=colors["dot"] if breathe > 0.58 else colors["dot_soft"])
        out.append(" ", style=colors["edge"])
        out.append(right_inner, style=colors["dot_soft"] if right_inner.strip() else colors["edge"])
        out.append(right_outer, style=colors["dot_dim"] if right_outer.strip() else colors["edge"])
        out.append(edge_right, style=colors["edge"])
        out.append(" ", style=colors["edge"])

        focus = ((math.sin((phase * 0.18) - 0.6) + 1.0) * 0.5) * max(1.0, len(text) - 1)
        for pos, char in enumerate(text):
            if char == " ":
                style = colors["text_dim"]
            else:
                distance = abs(pos - focus)
                style = colors["text"] if distance < 0.8 else colors["text_dim"]
            out.append(char, style=style)
        return out

    @classmethod
    def tool_status_refresh_per_second(cls) -> int:
        refresh_per_second = 18
        return refresh_per_second

    @classmethod
    def tool_status_interval(cls) -> float:
        interval = 1 / cls.tool_status_refresh_per_second()
        return interval

    @classmethod
    def tool_status_step(cls) -> float:
        step = 0.7
        return step

    @classmethod
    def _tool_status_indicator(cls, phase: float, *, subtle: bool = False) -> Text:
        colors = {
            "edge"  : "bold #6A6256",
            "shell" : "bold #C7B8A1",
            "core"  : "bold #FFF7E7",
            "pulse" : "bold #F2DEC0",
            "dust"  : "bold #8F816E"
        }
        if subtle:
            breathe = 0.46
            left_phase = 0.58
            right_phase = 0.58
            shell_phase = 0.0
        else:
            breathe = 0.5 + (0.5 * math.sin(phase * 0.52))
            left_phase = 0.5 + (0.5 * math.sin((phase * 0.52) - 0.95))
            right_phase = 0.5 + (0.5 * math.sin((phase * 0.52) + 0.95))
            shell_phase = phase * 0.72

        shell_frames = (("(", ")"), ("(", ")"), ("<", ">"), ("{", "}"), ("<", ">"))
        shell_left, shell_right = ("(", ")") if subtle else shell_frames[
            int(shell_phase) % len(shell_frames)
        ]

        if left_phase > 0.84:
            chamber_left = "o"
        elif left_phase > 0.72:
            chamber_left = "."
        else:
            chamber_left = " "

        if right_phase > 0.84:
            chamber_right = "o"
        elif right_phase > 0.72:
            chamber_right = "."
        else:
            chamber_right = " "

        if subtle:
            core = "o"
            echo_left = " "
            echo_right = " "
        else:
            if breathe > 0.88:
                core = "@"
            elif breathe > 0.72:
                core = "*"
            elif breathe > 0.58:
                core = "O"
            else:
                core = "o"

            if breathe > 0.9:
                echo_left = "o"
                echo_right = "o"
            elif breathe > 0.82:
                echo_left = "."
                echo_right = "."
            else:
                echo_left = " "
                echo_right = " "
        out = Text()

        out.append("[", style=colors["edge"])
        out.append(echo_left, style=colors["dust"] if echo_left.strip() else colors["edge"])
        out.append(chamber_left, style=colors["dust"])
        out.append(shell_left, style=colors["shell"] if breathe < 0.76 else colors["pulse"])
        out.append(core, style=colors["core"] if breathe > 0.58 else colors["pulse"])
        out.append(shell_right, style=colors["shell"] if breathe < 0.76 else colors["pulse"])
        out.append(chamber_right, style=colors["dust"])
        out.append(echo_right, style=colors["dust"] if echo_right.strip() else colors["edge"])
        out.append("]", style=colors["edge"])
        return out


if __name__ == '__main__':
    pass
