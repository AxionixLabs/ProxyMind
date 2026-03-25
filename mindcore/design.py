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

    async def prefix_line(self, stop_event: asyncio.Event) -> None:
        if self.design_level != const.SHOW_LEVEL:
            return None

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

        with Live(Text(" " * width), console=self.console, refresh_per_second=fps) as live:
            while not stop_event.is_set():
                t += 1
                frame = base_frame(t)
                await render(frame)
                await asyncio.sleep(1 / fps)

            await settle(t)
            await draw([" "] * width)

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
            match theme:
                case "fast":
                    chars, styles = build_fast(i)
                case "plan":
                    chars, styles = build_plan(i)
                case _:
                    chars, styles = build_chat(i)

            out = Text()
            prefix = f"{spin[i % len(spin)]} "
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


class TypewriterStreamSession(object):

    MIN_VIEW_LINES = 6
    VIEW_MARGIN    = 4

    def __init__(self, max_lines: int = 12) -> None:
        self.lines: deque = deque(maxlen=max_lines)
        self.out: str     = ""
        self.col: int     = 0
        self.delay: float = 0.01
        self.cursor: str  = random.choice(["█", "▉", "▋"])

        self.live: typing.Optional[Live] = None

    def _viewport_lines(self) -> int:
        height = max(0, int(getattr(Design.console, "height", 0) or 0))
        if height <= 0:
            return self.lines.maxlen
        return max(self.MIN_VIEW_LINES, min(self.lines.maxlen, height - self.VIEW_MARGIN))

    def _tail_text(self, text: str) -> str:
        max_lines = self._viewport_lines()
        if not text or max_lines <= 0:
            return text

        parts = text.split("\n")
        if text.endswith("\n"):
            rows = parts[-max_lines - 1:]
        else:
            rows = parts[-max_lines:]
        return "\n".join(rows)

    def _tail_renderable(self) -> Text:
        return Text(self._tail_text(self.out), style="bold")

    async def start(self) -> None:
        if self.live: return None
        self.live = Live(
            self._tail_renderable(),
            console=Design.console,
            refresh_per_second=12,
            transient=True,
            vertical_overflow="crop"
        )
        self.live.__enter__()

    async def stop(self) -> None:
        if self.live is not None:
            try:
                await Design.cursor_blink(
                    self.live, self.out, self.cursor, max_lines=self._viewport_lines()
                )
            finally:
                self.live.__exit__(None, None, None)
                self.live = None

        if self.out:
            Design.console.print(Text(self.out, style="bold"))
        Design.console.print()

    async def feed(self, delta: str) -> None:
        if not delta or not self.live:
            return None

        final_delay = max(0.0015, self.delay * 0.65)

        self.out, self.delay = await Design.typewriter(
            self.live, delta, self.out, self.delay, final_delay, self.cursor,
            max_lines=self._viewport_lines()
        )

    async def render(self, content: str) -> None:
        if self.live is None:
            return None

        self.out = content
        self.live.update(self._tail_renderable())

    async def sync(self, content: str, *, animate: bool = False) -> None:
        if self.live is None:
            return None

        if animate and content.startswith(self.out):
            delta = content[len(self.out):]
            if delta:
                return await self.feed(delta)
            return None

        await self.render(content)


if __name__ == '__main__':
    pass
