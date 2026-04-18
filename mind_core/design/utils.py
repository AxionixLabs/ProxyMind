# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import random
import typing
import asyncio
from pathlib import Path
from collections import deque
from rich.live import Live
from rich.text import Text
from rich.tree import Tree
from rich.console import Console
from mind_nova import const

DESIGN_CONSOLE: Console = Console()


def mix_hex_color(start: str, end: str, ratio: float) -> str:
    start_rgb = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
    end_rgb   = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
    clamped   = max(0.0, min(1.0, float(ratio)))

    mixed  = tuple(
        round(start_rgb[channel] + ((end_rgb[channel] - start_rgb[channel]) * clamped))
        for channel in range(3)
    )
    return f"#{mixed[0]:02X}{mixed[1]:02X}{mixed[2]:02X}"


def ease_in_out_sine(ratio: float) -> float:
    clamped = max(0.0, min(1.0, float(ratio)))
    return 0.5 - 0.5 * math.cos(math.pi * clamped)


class DesignDoc(object):
    """统一的终端输出门面。"""

    @classmethod
    def log(cls, text: typing.Any) -> None:
        DESIGN_CONSOLE.print(const.PRINT_HEAD, f"[bold]{text}")

    @classmethod
    def suc(cls, text: typing.Any) -> None:
        DESIGN_CONSOLE.print(const.PRINT_HEAD, f"{const.SUC}{text}")

    @classmethod
    def wrn(cls, text: typing.Any) -> None:
        DESIGN_CONSOLE.print(const.PRINT_HEAD, f"{const.WRN}{text}")

    @classmethod
    def err(cls, text: typing.Any) -> None:
        DESIGN_CONSOLE.print(const.PRINT_HEAD, f"{const.ERR}{text}")


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

    pause: float         = 0.025
    jitter: float        = 0.0012
    breathe_every: int   = 160
    breathe_pause: float = 0.06
    glitch: float        = 0.003
    flush_chars: int     = 2
    flush_ms: float      = 0.012

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
            parts = full_out.split("\n")

            if full_out.endswith("\n"):
                cur = ""
                lines = parts[:-1]
            else:
                cur = parts[-1] if parts else ""
                lines = parts[:-1]

            self.lines.clear()
            for ln in lines[-self.lines.maxlen:]:
                self.lines.append(ln)

            self.cur = cur

        def text(self, *, pad_to: typing.Optional[int] = None) -> str:
            rows = list(self.lines) + [self.cur]
            if pad_to and pad_to > 0 and len(rows) < pad_to:
                rows = [""] * (pad_to - len(rows)) + rows
            return "\n".join(rows)

    win: typing.Optional[Window] = Window(max_lines) if max_lines else None
    use_window = win is not None

    def render() -> None:
        text = win.text() if use_window else out
        live.update(render_fn(text, cursor))

    if use_window and out:
        win.set_from_out_tail(out)
        render()

    for i, ch in enumerate(delta):
        denominator = (n - 1) if n > 1 else 1
        d = begin_delay + (final_delay - begin_delay) * (i / denominator)
        d = max(0.0, d + random.uniform(-jitter, jitter))

        if ch in "，,":
            d += pause * 0.6
        elif ch in "。.!！?？":
            d += pause * 1.4
        elif ch in "；;：:":
            d += pause

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

        out += ch
        if use_window:
            win.append_char(ch)

        pending += 1
        now = loop.time()
        if pending >= flush_chars or (now - last_flush) >= flush_ms:
            render()
            last_flush, pending = now, 0

        if breathe_every and (len(out) % breathe_every == 0):
            d += breathe_pause

        await asyncio.sleep(d)

    if pending:
        render()

    return out, final_delay


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
        if not max_lines:
            return text
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


def build_file_tree(file_path: str, *, console: Console | None = None) -> None:
    """显示树状图。"""
    active_console = console or DESIGN_CONSOLE

    color_schemes = {
        "Ocean Breeze": ["#AFD7FF", "#87D7FF", "#5FAFD7"],
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

    root = parts[0]
    tree = Tree(
        f"[bold {text_color}]{choice_icon(root)} {root}[/]", guide_style=f"bold {root_color}"
    )
    current_path = parts[0]
    current_node = tree

    for part in parts[1:-1]:
        current_path = Path(current_path, part)
        current_node = current_node.add(
            f"[bold {text_color}]{choice_icon(current_path)} {part}[/]", guide_style=f"bold {folder_color}"
        )

    ext = (file := Path(parts[-1])).suffix.lower()
    current_node.add(f"[bold {file_color}]{choice_icon(ext)} {file.name}[/]")

    active_console.print(tree)


if __name__ == '__main__':
    pass
