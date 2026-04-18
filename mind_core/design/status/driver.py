# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
import asyncio
from rich.live import Live
from rich.text import Text
from rich.console import Console
from .agent_frames import (
    render_agent_connect_frame, render_agent_wait_frame
)
from .renderers import StatusRenderer
from .types import AgentLiveTheme
from mind_nova import const


class DesignStatusLiveDriver(StatusRenderer):

    design_level: str
    console: Console | None = None

    async def agent_wait_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], tuple[str, str]]
    ) -> None:
        """订阅模式呼吸等待动画。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        width = min(34, max(24, self.console.width - 22))
        theme = AgentLiveTheme(
            refresh_per_second=32,
            text_width=min(56, max(28, self.console.width - 10)),
            base_pad="  ",
            colors={
                "shell"      : "#223444",
                "shell_dim"  : "#13202C",
                "core"       : "#D9FCFF",
                "near"       : "#56D8E8",
                "beam"       : "#35C2DB",
                "beam_dim"   : "#35586A",
                "title"      : "#DDFBFF",
                "detail"     : "#94A6BA",
                "detail_dim" : "#60758C",
                "pulse"      : "#6BE2FF",
                "pulse_dim"  : "#3E6F8E"
            },
            default_title="Subscription Idle",
            default_detail="Waiting for link state"
        )
        tick = 0
        with Live(
            render_agent_wait_frame(0, snapshot(), width, theme),
            console=self.console,
            refresh_per_second=theme.refresh_per_second,
            transient=True
        ) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(render_agent_wait_frame(tick, snapshot(), width, theme))
                await asyncio.sleep(1 / theme.refresh_per_second)

    async def agent_connect_live(
        self,
        stop_event: asyncio.Event,
        snapshot: typing.Callable[[], tuple[str, str]]
    ) -> None:
        """订阅模式建连等待动画。"""
        if self.design_level != const.SHOW_LEVEL:
            return None

        width = min(34, max(24, self.console.width - 22))
        theme = AgentLiveTheme(
            refresh_per_second=30,
            text_width=min(56, max(28, self.console.width - 10)),
            base_pad="  ",
            colors={
                "pulse"      : "#7EDCFF",
                "pulse_dim"  : "#4E7A9D",
                "title"      : "#E8F9FF",
                "detail"     : "#A8B6C8",
                "detail_dim" : "#6E8095",
                "node"       : "#48C8FF",
                "node_hot"   : "#BDF4FF",
                "rail_dim"   : "#284055",
                "rail_hot"   : "#63D7F4"
            },
            default_title="Opening Fold Link",
            default_detail="Waiting for subscription handshake"
        )
        tick = 0
        with Live(
            render_agent_connect_frame(0, snapshot(), width, theme),
            console=self.console,
            refresh_per_second=theme.refresh_per_second,
            transient=True
        ) as live:
            while not stop_event.is_set():
                tick += 1
                live.update(render_agent_connect_frame(tick, snapshot(), width, theme))
                await asyncio.sleep(1 / theme.refresh_per_second)

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
                    "spin": "◜◠◝◞◡◟",
                    "bubble_left": "〈《(",
                    "bubble_right": ")》〉",
                    "bubble_dot": "●◉",
                    "focus": "◆",
                    "pulse": "•",
                    "echo": "·",
                    "beam_a": "═",
                    "beam_b": "─",
                    "noise": "˙",
                    "tail": "•",
                    "trail": "⋅",
                    "reply": "◦◎",
                    "listen": "◌◍",
                    "speak": "◉◍"
                },
                "colors": {
                    "prefix": "#A3E635",
                    "core": "#C4FFF0",
                    "near": "#9EF7E7",
                    "beam": "#67E8F9",
                    "beam_dim": "#3F9FB3",
                    "dust": "#3F3F46",
                    "sweep_core": "#93C5FD",
                    "sweep_tail": "#60A5FA",
                    "shell": "#244454",
                    "shell_dim": "#22313A",
                    "orbit_a": "#FDE68A",
                    "orbit_b": "#8BE9FD",
                    "orbit_c": "#5EEAD4"
                },
                "motion": {
                    "phase_div": 4.2,
                    "lead_freq": 1.08,
                    "reply_freq": 0.72,
                    "reply_phase": 1.45,
                    "breathe_freq": 0.88,
                    "sender_freq": 1.62,
                    "receiver_freq": 1.28,
                    "receiver_phase": 2.2,
                    "chat_cycle": 16
                }
            },
            "fast": {
                "glyphs": {
                    "spin": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
                    "packet": "◈",
                    "core": "◆",
                    "near": "•",
                    "beam_a": "=",
                    "beam_b": "-",
                    "trail": ":",
                    "echo": "~",
                    "gate": ">",
                    "dust": "˙",
                    "orbit": ".",
                    "glitch": "·:~"
                },
                "colors": {
                    "prefix": "#F59E0B",
                    "core": "#FFF3C4",
                    "near": "#FCD34D",
                    "beam": "#FB7185",
                    "beam_dim": "#BE5672",
                    "dust": "#4A2D33",
                    "sweep_core": "#F97316",
                    "sweep_tail": "#FB7185",
                    "shell": "#5B2C1A",
                    "shell_dim": "#3A2320",
                    "orbit_a": "#FDBA74",
                    "orbit_b": "#F472B6",
                    "orbit_c": "#FDE68A"
                },
                "motion": {
                    "phase_div": 3.0,
                    "lead_freq": 0.92,
                    "echo_phase": 0.85,
                    "pilot_freq": 1.8,
                    "pilot_offset": 3.0,
                    "pilot_amp": 1.5
                }
            },
            "plan": {
                "glyphs": {
                    "spin": "◴◷◶◵",
                    "done": "◆",
                    "active": "◉",
                    "next": "◇",
                    "idle": "○",
                    "beam_a": "═",
                    "beam_b": "─",
                    "progress": "▸",
                    "pulse": "•",
                    "echo": "·"
                },
                "colors": {
                    "prefix": "#34D399",
                    "core": "#D1FAE5",
                    "near": "#6EE7B7",
                    "beam": "#A7F3D0",
                    "beam_dim": "#4E9F8A",
                    "dust": "#31403D",
                    "sweep_core": "#10B981",
                    "sweep_tail": "#34D399",
                    "shell": "#1F4D45",
                    "shell_dim": "#203733",
                    "orbit_a": "#A7F3D0",
                    "orbit_b": "#93C5FD",
                    "orbit_c": "#C4B5FD"
                },
                "motion": {
                    "phase_div": 7.2,
                    "active_freq": 0.8,
                    "bridge_freq": 1.2
                }
            }
        }
        palette = palettes.get(theme, palettes["chat"])

        glyphs: dict[str, str] = palette["glyphs"]
        colors: dict[str, str] = palette["colors"]
        motion: dict[str, float] = palette["motion"]
        width = min(30, max(22, self.console.width - 22))

        fps = 32
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

            styles: dict[int, str] = {}

            left_shell       = glyphs["bubble_left"][1 if sender_talk else (2 if sender_release else 0)]
            right_shell      = glyphs["bubble_right"][1 if receiver_ack else (2 if receiver_listen else 0)]
            left_dot_idle    = glyphs["bubble_dot"][0]
            left_dot_talk    = glyphs["speak"][0] if (i % 4) < 2 else glyphs["speak"][1]
            right_dot_idle   = glyphs["listen"][0]
            right_dot_listen = glyphs["listen"][1] if (i % 4) < 2 else glyphs["pulse"]
            reply_glyph      = glyphs["reply"][1] if (i % 4) < 2 else glyphs["reply"][0]

            chars[left] = left_shell
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

            chars[right] = right_shell
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
            lead = 0.5 + 0.5 * math.sin(phase * motion["lead_freq"])
            head = clamp(int(lead * (width - 1)))
            echo = clamp(
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
            cols = [2, width // 3, (2 * width) // 3, width - 3]
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


if __name__ == '__main__':
    pass
