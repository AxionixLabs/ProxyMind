# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
import asyncio
from dataclasses import dataclass
from rich.cells import cell_len
from rich.live import Live
from rich.text import Text
from rich.console import Console
from .utils import (
    mix_hex_color, ease_in_out_sine
)
from mind_nova import const


@dataclass(frozen=True)
class SweepStatusSpec(object):
    refresh_per_second: int
    phase_rate: float
    text_limit: int
    shell_freq: float
    lead_span: float
    tail_span: float
    peak_radius: float
    near_ratio: float
    mid_ratio: float
    scan_speed: float = 0.0
    scan_pad: float = 0.0
    drift_wobble_amp: float = 0.0
    drift_wobble_freq: float = 0.0
    drift_offset: float = 0.0
    entry_pad: float = 0.0
    exit_pad: float = 0.0


@dataclass(frozen=True)
class ProgressiveStatusSpec(object):
    refresh_per_second: int
    phase_rate: float
    text_limit: int
    shell_freq: float
    head_speed: float
    cycle_padding: float
    head_offset: float
    tail_reset: float
    lead_glow: float
    tail_glow: float


@dataclass(frozen=True)
class AgentLiveTheme(object):
    refresh_per_second: int
    text_width: int
    base_pad: str
    colors: dict[str, str]
    default_title: str
    default_detail: str


def _fit_status_text(text: str, text_width: int) -> str:
    raw = (text or "").strip()
    if cell_len(raw) <= text_width:
        return raw
    trimmed = raw
    while trimmed and cell_len(trimmed + "…") > text_width:
        trimmed = trimmed[:-1]
    return trimmed + "…"


def _prefix_text(symbol: str, style: str) -> Text:
    out = Text()
    out.append(symbol, style=style)
    out.append(" " * max(1, 3 - cell_len(symbol)))
    return out


def render_agent_wait_frame(
    frame_idx: int,
    snapshot: tuple[str, str],
    width: int,
    theme: AgentLiveTheme
) -> Text:
    title, detail = snapshot

    fps           = theme.refresh_per_second
    colors        = theme.colors
    phase         = frame_idx / fps
    breathe       = 0.5 + 0.5 * math.sin(phase * 1.85)
    left_breathe  = 0.5 + 0.5 * math.sin(phase * 1.85 - 0.9)
    right_breathe = 0.5 + 0.5 * math.sin(phase * 1.85 + 0.9)

    sweep_primary = 0.5 + 0.5 * (
        0.58 * math.sin(phase * 2.1)
        + 0.28 * math.sin(phase * 1.17 + 1.1)
        + 0.14 * math.cos(phase * 0.63 + 2.0)
    )
    sweep_echo = 0.5 + 0.5 * math.sin(phase * 1.72 + 2.1)
    title_glow = 0.48 + breathe * 0.24
    detail_glow = 0.18 + breathe * 0.18
    pulse_glow = 0.30 + breathe * 0.30
    title_tone = ease_in_out_sine(title_glow)
    detail_tone = ease_in_out_sine(detail_glow)
    pulse_tone = ease_in_out_sine(pulse_glow)
    pulse_frames = ("⠈", "⠐", "⠠", "⢀", "⡀", "⠄", "⠂", "⠁", "⠂", "⠄", "⡀", "⢀", "⠠", "⠐")
    detail_frames = ("·", "∙", "•", "∙", "·", "◦")

    pulse_phase = (
        (phase * 13.6)
        + (ease_in_out_sine(0.5 + 0.5 * math.sin(phase * 4.2 - 0.35)) * 2.2)
        + (0.12 * math.sin(phase * 5.8))
    )
    spin_phase = (
        (phase * 9.4)
        + (ease_in_out_sine(0.5 + 0.5 * math.sin(phase * 2.8 + 0.45)) * 0.9)
    )

    pulse = pulse_frames[int(pulse_phase) % len(pulse_frames)]
    spin  = detail_frames[int(spin_phase) % len(detail_frames)]
    chars = [" "] * width

    styles: dict[int, str] = {}

    left   = 2
    center = width // 2
    right  = width - 3

    trail_left  = left + 2
    trail_right = right - 2

    head_primary        = trail_left + int((trail_right - trail_left) * sweep_primary)
    head_echo           = trail_left + int((trail_right - trail_left) * sweep_echo)
    ambient_radius      = 3 + round(breathe * 2.0)
    center_quiet_radius = 1 + round((1.0 - breathe) * 1.4)

    chars[left]   = "◍" if left_breathe > 0.72 else "◌"
    styles[left]  = f"bold {mix_hex_color(colors['shell_dim'], colors['near'], 0.18 + left_breathe * 0.42)}"
    chars[right]  = "◍" if right_breathe > 0.72 else "◌"
    styles[right] = f"bold {mix_hex_color(colors['shell_dim'], colors['near'], 0.18 + right_breathe * 0.42)}"

    for pos in range(left + 2, right - 1):
        primary_distance = abs(pos - head_primary)
        echo_distance = abs(pos - head_echo)
        ambient_ratio = max(
            0.0,
            1.0 - (abs(pos - center) / max(1, ambient_radius + 1))
        )
        center_distance = abs(pos - center)
        if primary_distance == 0:
            chars[pos] = "•"
            styles[pos] = f"bold {colors['core']}"
        elif echo_distance == 0:
            chars[pos] = "∙"
            styles[pos] = f"bold {mix_hex_color(colors['beam_dim'], colors['beam'], 0.82)}"
        elif 0 < primary_distance <= 3:
            chars[pos] = "·"
            styles[pos] = f"bold {colors['beam']}"
        elif echo_distance in (1, 2) and (pos + frame_idx) % 2 == 0:
            chars[pos] = "·"
            styles[pos] = f"bold {mix_hex_color(colors['beam_dim'], colors['beam'], 0.56)}"
        elif primary_distance <= 6 and (pos + frame_idx) % 2 == 0:
            chars[pos] = "·"
            styles[pos] = f"bold {colors['beam_dim']}"
        elif (
            ambient_ratio > 0.54
            and center_distance > center_quiet_radius
            and (pos + frame_idx) % 5 in (0, 2)
        ):
            chars[pos] = "∙" if ambient_ratio > 0.66 else "·"
            styles[pos] = (
                f"bold {mix_hex_color(colors['beam_dim'], colors['pulse'], 0.10 + ambient_ratio * 0.28)}"
            )
        elif (pos + frame_idx) % 11 == 0 and trail_left <= pos <= trail_right:
            chars[pos] = "·"
            styles[pos] = f"bold {colors['beam_dim']}"

    chars[center]  = "◎" if breathe > 0.55 else "◉"
    styles[center] = f"bold {mix_hex_color(colors['near'], colors['core'], 0.55 + breathe * 0.45)}"

    line1 = Text()
    line1.append(theme.base_pad)
    for cell_idx, glyph in enumerate(chars):
        line1.append(glyph, style=styles.get(cell_idx, ""))

    line2 = Text()
    line2.append(theme.base_pad)
    line2.append_text(
        _prefix_text(
            pulse,
            f"bold {mix_hex_color(colors['pulse_dim'], colors['pulse'], pulse_tone)}"
        )
    )
    line2.append(
        _fit_status_text(title or theme.default_title, theme.text_width),
        style=f"bold {mix_hex_color(colors['detail'], colors['title'], title_tone)}"
    )

    line3 = Text()
    line3.append(theme.base_pad)
    line3.append_text(
        _prefix_text(
            spin,
            f"bold {mix_hex_color(colors['shell_dim'], colors['pulse_dim'], detail_tone)}"
        )
    )
    line3.append(
        _fit_status_text(detail or theme.default_detail, theme.text_width),
        style=f"{mix_hex_color(colors['detail_dim'], colors['detail'], detail_tone)}"
    )

    out = Text(no_wrap=True, overflow="crop")
    out.append_text(line1)
    out.append("\n")
    out.append_text(line2)
    out.append("\n")
    out.append_text(line3)
    return out


def render_agent_connect_frame(
    frame_idx: int,
    snapshot: tuple[str, str],
    width: int,
    theme: AgentLiveTheme
) -> Text:
    title, detail = snapshot

    fps           = theme.refresh_per_second
    colors        = theme.colors
    phase         = frame_idx / fps
    breathe       = 0.5 + 0.5 * math.sin(phase * 2.2)
    scan          = ease_in_out_sine(0.5 + 0.5 * math.sin(phase * 3.8))
    echo_scan     = ease_in_out_sine(0.5 + 0.5 * math.sin(phase * 3.8 + 1.55))
    bridge_focus  = ease_in_out_sine(scan)
    title_glow    = 0.58 + breathe * 0.18
    detail_glow   = 0.24 + breathe * 0.12
    pulse_glow    = 0.42 + breathe * 0.20
    title_tone    = ease_in_out_sine(title_glow)
    detail_tone   = ease_in_out_sine(detail_glow)
    pulse_tone    = ease_in_out_sine(pulse_glow)
    spin_frames   = ("⠋", "⠙", "⠚", "⠒", "⠂", "⠆", "⠖", "⠶", "⠴", "⠦", "⠇", "⠏")
    detail_frames = ("·", "∙", "•", "∙", "·", "◦")

    spin_phase = (
        (phase * 14.2)
        + (ease_in_out_sine(0.5 + 0.5 * math.sin(phase * 4.0)) * 2.4)
        + (0.16 * math.sin(phase * 6.0 + 0.2))
    )
    detail_phase = (
        (phase * 9.6)
        + (ease_in_out_sine(0.5 + 0.5 * math.sin(phase * 2.9 + 1.0)) * 0.8)
    )

    chars = [" "] * width

    styles: dict[int, str] = {}

    left   = 1
    center = width // 2
    right  = width - 2

    left_lane         = list(range(left + 1, center))
    right_lane        = list(range(center + 1, right))
    head              = min(len(left_lane) - 1, max(0, round(scan * (len(left_lane) - 1))))
    echo_head         = min(len(left_lane) - 1, max(0, round(echo_scan * (len(left_lane) - 1))))
    prev_phase        = max(0.0, phase - (1 / fps))
    prev_scan         = ease_in_out_sine(0.5 + 0.5 * math.sin(prev_phase * 3.8))
    prev_bridge_focus = ease_in_out_sine(prev_scan)
    focus_rising      = bridge_focus >= prev_bridge_focus
    hot_threshold     = 0.82 if focus_rising else 0.70
    warm_threshold    = 0.62 if focus_rising else 0.48

    center_char = "◆" if bridge_focus >= hot_threshold else ("◈" if bridge_focus >= warm_threshold else "◇")
    center_tone = (
        0.50
        + (bridge_focus * 0.24)
        + (breathe * 0.16)
        + (0.08 if center_char == "◆" else (0.03 if center_char == "◈" else 0.0))
    )

    chars[left]    = "◉" if breathe > 0.48 else "◎"
    styles[left]   = f"bold {mix_hex_color(colors['node'], colors['node_hot'], 0.35 + (0.5 + 0.5 * math.sin(phase * 2.2 - 0.8)) * 0.55)}"
    chars[center]  = center_char
    styles[center] = f"bold {mix_hex_color(colors['node'], colors['node_hot'], center_tone)}"
    chars[right]   = "◉" if breathe > 0.48 else "◎"
    styles[right]  = f"bold {mix_hex_color(colors['node'], colors['node_hot'], 0.35 + (0.5 + 0.5 * math.sin(phase * 2.2 + 0.8)) * 0.55)}"

    def paint_lane(
        lane: list[int],
        active_head: int,
        echo_head_idx: int
    ) -> None:
        for lane_idx, pos in enumerate(lane):
            primary_ratio = max(0.0, 1.0 - (abs(lane_idx - active_head) / 4.0))
            echo_ratio    = max(0.0, 1.0 - (abs(lane_idx - echo_head_idx) / 3.0))
            bridge_ratio  = max(0.0, 1.0 - (abs(pos - center) / 4.0)) * bridge_focus
            lane_glyph    = "─"
            ratio         = 0.16 + (bridge_ratio * 0.18)

            if primary_ratio >= 0.76:
                lane_glyph = "═"
                ratio = 1.0
            elif primary_ratio >= 0.38:
                lane_glyph = "─"
                ratio = max(ratio, 0.34 + (primary_ratio * 0.42))
            elif echo_ratio >= 0.58 and (lane_idx + frame_idx) % 2 == 0:
                lane_glyph = "╌"
                ratio = max(ratio, 0.26 + (echo_ratio * 0.34))
            elif bridge_ratio > 0.42 and (pos + frame_idx) % 3 != 1:
                lane_glyph = "·"
                ratio = max(ratio, 0.20 + (bridge_ratio * 0.28))

            chars[pos]  = lane_glyph
            styles[pos] = f"bold {mix_hex_color(colors['rail_dim'], colors['rail_hot'], ratio)}"

    mirrored_head      = len(right_lane) - 1 - head
    mirrored_echo_head = len(right_lane) - 1 - echo_head

    paint_lane(left_lane, head, echo_head)
    paint_lane(right_lane, mirrored_head, mirrored_echo_head)

    line1 = Text()
    line1.append(theme.base_pad)
    for cell_idx, glyph in enumerate(chars):
        line1.append(glyph, style=styles.get(cell_idx, ""))

    line2 = Text()
    line2.append(theme.base_pad)
    line2.append_text(
        _prefix_text(
            spin_frames[int(spin_phase) % len(spin_frames)],
            f"bold {mix_hex_color(colors['pulse_dim'], colors['pulse'], pulse_tone)}"
        )
    )
    line2.append(
        _fit_status_text(title or theme.default_title, theme.text_width),
        style=f"bold {mix_hex_color(colors['detail'], colors['title'], title_tone)}"
    )

    line3 = Text()
    line3.append(theme.base_pad)
    line3.append_text(
        _prefix_text(
            detail_frames[int(detail_phase) % len(detail_frames)],
            f"bold {mix_hex_color(colors['detail_dim'], colors['pulse_dim'], detail_tone)}"
        )
    )
    line3.append(
        _fit_status_text(detail or theme.default_detail, theme.text_width),
        style=f"{mix_hex_color(colors['detail_dim'], colors['detail'], detail_tone)}"
    )

    out = Text(no_wrap=True, overflow="crop")
    out.append_text(line1)
    out.append("\n")
    out.append_text(line2)
    out.append("\n")
    out.append_text(line3)
    return out


class DesignStatusLiveDriver(object):

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

    @classmethod
    def tool_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("tool")

        colors = {
            "edge"      : "bold #6A6256",
            "shell"     : "bold #B9AB96",
            "core"      : "bold #EDE2CE",
            "pulse"     : "bold #DCC8AB",
            "dust"      : "bold #857866",
            "text_peak" : "bold #E6D7BF",
            "text_soft" : "bold #DFD0B8",
            "text_near" : "bold #D7C7AF",
            "text_mid"  : "bold #C5B094",
            "text_fade" : "bold #AB967F",
            "text_dim"  : "bold #8C7B6A"
        }

        text = cls.fit_status_text(text, kind="tool", fallback="function calling")
        span = max(1, len(text))

        sweep = cls._sway_focus(
            phase,
            span,
            speed=spec.scan_speed,
            pad=spec.scan_pad
        )

        out = cls._tool_status_indicator(
            phase,
            breathe_freq=0.30,
            frame_rate=0.12
        )
        out.append(cls.status_content_gap(), style=colors["edge"])
        cls._append_sweep_text(
            out,
            text,
            focus=sweep,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            lead_span=spec.lead_span,
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius,
            soft_ratio=0.22,
            near_ratio=spec.near_ratio,
            mid_ratio=spec.mid_ratio
        )
        return out

    @classmethod
    def tool_status_static_renderable(cls, text: str) -> Text:
        return cls.tool_status_renderable(0.0, text)

    @classmethod
    def builtin_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("builtin")

        status_shell_motion_scale    = 0.76
        status_inner_solid_threshold = 0.87
        status_inner_soft_threshold  = 0.70
        status_outer_solid_threshold = 0.92
        status_outer_soft_threshold  = 0.77
        status_edge_threshold        = 0.83

        stable_colors = {
            "edge"      : "bold #40515D",
            "core"      : "bold #DCE9ED",
            "near"      : "bold #B3CAD3",
            "trail"     : "bold #78949F",
            "dust"      : "bold #4C606B",
            "text"      : "bold #DCEAF0",
            "text_soft" : "bold #D2E3E9",
            "text_near" : "bold #C2D7DE",
            "text_mid"  : "bold #9DB8C2",
            "text_fade" : "bold #697F89",
            "text_dim"  : "bold #53656E"
        }

        text   = cls.fit_status_text(text, kind="builtin", fallback="working")

        colors            = stable_colors
        shell_motion      = phase * (spec.shell_freq * status_shell_motion_scale)
        breathe           = 0.5 + (0.5 * math.sin(shell_motion))
        left_outer_phase  = 0.5 + (0.5 * math.sin(shell_motion - 1.45))
        left_inner_phase  = 0.5 + (0.5 * math.sin(shell_motion - 0.75))
        right_inner_phase = 0.5 + (0.5 * math.sin(shell_motion + 0.75))
        right_outer_phase = 0.5 + (0.5 * math.sin(shell_motion + 1.45))
        shell_phase       = 0.5 + (0.5 * math.sin(shell_motion + 2.1))
        span              = max(1, len(text))

        drift = cls._drift_focus(
            phase,
            span,
            entry_pad=spec.entry_pad,
            exit_pad=spec.exit_pad
        )

        core = cls._status_core_char(
            breathe,
            peak="*",
            high="O",
            mid="o",
            low="."
        )
        left_inner = cls._status_shell_char(
            left_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_inner = cls._status_shell_char(
            right_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        left_outer = cls._status_shell_char(
            left_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        right_outer = cls._status_shell_char(
            right_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        edge_left, edge_right = cls._status_edge_pair(shell_phase, threshold=status_edge_threshold)

        out = cls._build_status_shell(
            edge_left=edge_left,
            edge_right=edge_right,
            left_outer=left_outer,
            left_inner=left_inner,
            core=core,
            right_inner=right_inner,
            right_outer=right_outer,
            edge_style=colors["edge"],
            outer_style=colors["trail"],
            inner_style=colors["near"],
            core_style=colors["core"]
        )
        out.append(cls.status_content_gap(), style=colors["edge"])
        cls._append_gradient_sweep_text(
            out,
            text,
            focus=drift,
            peak_color="#EEF9FD",
            soft_color="#E5F3F8",
            near_color="#D7EDF5",
            mid_color="#B3CDD8",
            fade_color="#708894",
            dim_color="#566C78",
            lead_span=spec.lead_span,
            tail_span=spec.tail_span,
            peak_radius=spec.peak_radius
        )
        return out

    @classmethod
    def thinking_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("wait")

        status_shell_motion_scale    = 0.76
        status_inner_solid_threshold = 0.87
        status_inner_soft_threshold  = 0.70
        status_outer_solid_threshold = 0.92
        status_outer_soft_threshold  = 0.77
        status_edge_threshold        = 0.83

        colors = {
            "edge"      : "bold #445856",
            "dot"       : "bold #D7E6E1",
            "dot_soft"  : "bold #B5CAC4",
            "dot_dim"   : "bold #748A85",
            "text"      : "bold #DDE7E3",
            "text_near" : "bold #A4B5B0",
            "text_tail" : "bold #667873",
            "text_dim"  : "bold #465652"
        }

        text = cls.fit_status_text(text, kind="wait", fallback="thinking")

        shell_motion      = phase * (spec.shell_freq * status_shell_motion_scale)
        breathe           = 0.5 + (0.5 * math.sin(shell_motion))
        left_outer_phase  = 0.5 + (0.5 * math.sin(shell_motion - 1.7))
        left_inner_phase  = 0.5 + (0.5 * math.sin(shell_motion - 0.9))
        right_inner_phase = 0.5 + (0.5 * math.sin(shell_motion + 0.9))
        right_outer_phase = 0.5 + (0.5 * math.sin(shell_motion + 1.7))
        shell_phase       = 0.5 + (0.5 * math.sin(shell_motion + 2.2))

        left_outer = cls._status_shell_char(
            left_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        left_inner = cls._status_shell_char(
            left_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_inner = cls._status_shell_char(
            right_inner_phase,
            solid_threshold=status_inner_solid_threshold,
            soft_threshold=status_inner_soft_threshold
        )
        right_outer = cls._status_shell_char(
            right_outer_phase,
            solid_threshold=status_outer_solid_threshold,
            soft_threshold=status_outer_soft_threshold
        )
        core = cls._status_core_char(
            breathe,
            peak="*",
            high="O",
            mid="o",
            low="."
        )
        edge_left, edge_right = cls._status_edge_pair(shell_phase, threshold=status_edge_threshold)

        out = cls._build_status_shell(
            edge_left=edge_left,
            edge_right=edge_right,
            left_outer=left_outer,
            left_inner=left_inner,
            core=core,
            right_inner=right_inner,
            right_outer=right_outer,
            edge_style=colors["edge"],
            outer_style=colors["dot_dim"],
            inner_style=colors["dot_soft"],
            core_style=colors["dot"] if breathe > 0.60 else colors["dot_soft"]
        )
        out.append(cls.status_content_gap(), style=colors["edge"])

        span = max(1, len(text))
        head = (
            (phase * spec.head_speed) % max(1.0, float((span * 2) + spec.cycle_padding))
        ) + spec.head_offset
        if head < float(span - 1):
            tail = spec.tail_reset
        else:
            tail = head - max(0.0, float(span - 1))
        cls._append_progressive_text(
            out,
            text,
            head=head,
            tail=tail,
            peak_style=colors["text"],
            near_style=colors["text_near"],
            tail_style=colors["text_tail"],
            dim_style=colors["text_dim"],
            lead_glow=spec.lead_glow,
            tail_glow=spec.tail_glow
        )
        return out

    @classmethod
    def heal_status_renderable(cls, phase: float, text: str) -> Text:
        spec = cls.status_spec("heal")

        colors = {
            "edge"      : "bold #3C535B",
            "shell"     : "bold #58C2CF",
            "pulse"     : "bold #8CEAF4",
            "core"      : "bold #E2FCFF",
            "dust"      : "bold #486A71",
            "text_peak" : "bold #EEFCFF",
            "text_soft" : "bold #D7F7FB",
            "text_near" : "bold #B4E6ED",
            "text_mid"  : "bold #81B1B9",
            "text_fade" : "bold #5B7C83",
            "text_dim"  : "bold #455C62"
        }

        out = cls._heal_status_indicator(
            phase,
            pulse_freq=0.84,
            frame_rate=0.38
        )
        text = cls.fit_status_text(text, kind="heal", fallback="restoring signal")
        span = max(1, len(text))
        travel = max(1.0, float(span - 1) + (spec.scan_pad * 2.0))
        progress = 0.5 - (0.5 * math.cos(phase * spec.scan_speed))
        lock_progress = cls._smoothstep(progress)
        focus = (lock_progress * travel) - spec.scan_pad
        mirror_focus = max(0.0, float(span - 1) - focus)

        out.append(cls.status_content_gap(), style=colors["edge"])
        cls._append_repair_text(
            out,
            text,
            left_focus=focus,
            right_focus=mirror_focus,
            peak_style=colors["text_peak"],
            soft_style=colors["text_soft"],
            near_style=colors["text_near"],
            mid_style=colors["text_mid"],
            fade_style=colors["text_fade"],
            dim_style=colors["text_dim"],
            near_span=spec.lead_span,
            fade_span=spec.tail_span,
            peak_radius=spec.peak_radius,
            lock_radius=1.35
        )
        return out

    @classmethod
    def status_spec(cls, kind: str) -> SweepStatusSpec | ProgressiveStatusSpec:
        tool_status_spec = SweepStatusSpec(
            refresh_per_second=40,
            phase_rate=14.4,
            text_limit=48,
            shell_freq=0.52,
            lead_span=3.1,
            tail_span=6.8,
            peak_radius=0.74,
            near_ratio=0.56,
            mid_ratio=0.90,
            scan_speed=0.2,
            scan_pad=2.6
        )
        builtin_status_spec = SweepStatusSpec(
            refresh_per_second=40,
            phase_rate=15.8,
            text_limit=48,
            shell_freq=0.55,
            lead_span=3.4,
            tail_span=6.6,
            peak_radius=0.78,
            near_ratio=0.46,
            mid_ratio=0.88,
            drift_wobble_amp=0.0,
            drift_wobble_freq=0.0,
            drift_offset=0.0,
            entry_pad=6.0,
            exit_pad=8.4
        )
        heal_status_spec = SweepStatusSpec(
            refresh_per_second=34,
            phase_rate=16.8,
            text_limit=56,
            shell_freq=0.68,
            lead_span=3.0,
            tail_span=5.8,
            peak_radius=0.72,
            near_ratio=0.50,
            mid_ratio=0.86,
            scan_speed=0.28,
            scan_pad=2.2
        )
        thinking_status_spec = ProgressiveStatusSpec(
            refresh_per_second=24,
            phase_rate=19.2,
            text_limit=48,
            shell_freq=0.48,
            head_speed=0.78,
            cycle_padding=4.0,
            head_offset=-1.2,
            tail_reset=-1.6,
            lead_glow=0.36,
            tail_glow=1.0
        )
        if kind == "tool":
            return tool_status_spec
        if kind == "heal":
            return heal_status_spec
        if kind == "wait":
            return thinking_status_spec
        return builtin_status_spec

    @classmethod
    def status_text_limit(cls, kind: str) -> int:
        console_width = 80
        if cls.console is not None:
            console_width = max(24, int(cls.console.width))

        spec = cls.status_spec(kind)
        if kind == "tool":
            chrome_width = 18
        elif kind == "heal":
            chrome_width = 18
        elif kind == "wait":
            chrome_width = 17
        else:
            chrome_width = 17

        visible_limit = max(12, console_width - chrome_width)
        return min(spec.text_limit, visible_limit)

    @staticmethod
    def status_text_floor(kind: str) -> int:
        if kind == "tool":
            return 16
        if kind == "heal":
            return 18
        if kind == "wait":
            return 12
        return 12

    @classmethod
    def fit_status_text(cls, text: typing.Any, *, kind: str, fallback: str) -> str:
        fitted = cls.truncate_status_text(text, limit=cls.status_text_limit(kind)) or str(fallback)
        return cls.pad_status_text(fitted, kind=kind)

    @classmethod
    def pad_status_text(cls, text: str, *, kind: str) -> str:
        normalized = str(text or "")
        if not normalized:
            return ""

        floor = min(cls.status_text_limit(kind), cls.status_text_floor(kind))
        used_width = cell_len(normalized)
        if used_width >= floor:
            return normalized

        return f"{normalized}{' ' * (floor - used_width)}"

    @classmethod
    def truncate_status_text(cls, text: typing.Any, *, limit: int) -> str:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return ""
        width_limit = max(1, int(limit))
        if cell_len(normalized) <= width_limit:
            return normalized

        ellipsis_char = "…"
        ellipsis_width = cell_len(ellipsis_char)
        if width_limit <= ellipsis_width:
            return ellipsis_char

        body_limit = width_limit - ellipsis_width
        body_chars: list[str] = []
        used_width = 0

        for char in normalized:
            char_width = cell_len(char)
            if used_width + char_width > body_limit:
                break
            body_chars.append(char)
            used_width += char_width

        body = "".join(body_chars).rstrip()
        if not body:
            return ellipsis_char
        return f"{body}{ellipsis_char}"

    @classmethod
    def _append_sweep_text(
        cls,
        out: Text,
        text: str,
        *,
        focus: float,
        peak_style: str,
        dim_style: str,
        soft_style: str | None = None,
        near_style: str | None = None,
        mid_style: str | None = None,
        fade_style: str | None = None,
        lead_span: float = 2.0,
        tail_span: float = 3.5,
        peak_radius: float = 0.7,
        soft_ratio: float = 0.0,
        near_ratio: float = 0.42,
        mid_ratio: float = 1.0
    ) -> None:
        soft_style = soft_style or peak_style
        near_style = near_style or peak_style
        mid_style  = mid_style or dim_style
        fade_style = fade_style or dim_style

        for pos, char in enumerate(text):
            delta      = pos - focus
            span       = lead_span if delta >= 0 else tail_span
            distance   = abs(delta)
            soft_limit = peak_radius + (span * max(0.0, soft_ratio))
            near_limit = peak_radius + (span * max(0.0, near_ratio))
            mid_limit  = peak_radius + (span * max(0.0, mid_ratio))

            if distance <= peak_radius:
                style = peak_style
            elif distance <= soft_limit:
                style = soft_style
            elif distance <= near_limit:
                style = near_style
            elif distance <= mid_limit:
                style = mid_style
            elif distance <= peak_radius + span:
                style = fade_style
            else:
                style = dim_style

            out.append(char, style=style)

    @classmethod
    def _append_gradient_sweep_text(
        cls,
        out: Text,
        text: str,
        *,
        focus: float,
        peak_color: str,
        soft_color: str,
        near_color: str,
        mid_color: str,
        fade_color: str,
        dim_color: str,
        lead_span: float,
        tail_span: float,
        peak_radius: float
    ) -> None:
        stops = (
            (0.00, dim_color),
            (0.18, fade_color),
            (0.44, mid_color),
            (0.72, near_color),
            (0.90, soft_color),
            (1.00, peak_color),
        )

        for pos, char in enumerate(text):
            distance = abs(pos - focus)
            span = lead_span if pos >= focus else tail_span
            if distance <= peak_radius:
                intensity = 1.0
            else:
                falloff = max(0.001, span)
                normalized = min(1.0, (distance - peak_radius) / falloff)
                intensity = 1.0 - cls._smoothstep(normalized)
                if pos < focus:
                    intensity *= 0.95

            out.append(char, style=cls._gradient_text_style(stops, intensity))

    @classmethod
    def _drift_focus(
        cls,
        phase: float,
        span: int,
        *,
        entry_pad: float,
        exit_pad: float
    ) -> float:
        left_pad  = max(0.0, float(entry_pad))
        right_pad = max(0.0, float(exit_pad))
        travel    = max(1.0, float(max(0, span - 1)) + left_pad + right_pad)

        return (phase % travel) - left_pad

    @classmethod
    def _gradient_text_style(
        cls,
        stops: tuple[tuple[float, str], ...],
        intensity: float
    ) -> str:
        level = max(0.0, min(1.0, float(intensity)))
        if level <= stops[0][0]:
            return f"bold {stops[0][1]}"

        for index in range(1, len(stops)):
            end_level, end_color = stops[index]
            start_level, start_color = stops[index - 1]
            if level <= end_level:
                span = max(0.0001, end_level - start_level)
                local = (level - start_level) / span
                eased = cls._smoothstep(local)
                color = mix_hex_color(start_color, end_color, eased)
                return f"bold {color}"

        return f"bold {stops[-1][1]}"

    @staticmethod
    def _smoothstep(value: float) -> float:
        clamped = max(0.0, min(1.0, float(value)))
        return clamped * clamped * (3.0 - (2.0 * clamped))

    @staticmethod
    def status_content_gap() -> str:
        return "  "

    @staticmethod
    def status_line_prefix() -> str:
        return " "

    @staticmethod
    def status_elapsed_separator() -> str:
        return "  · "

    @classmethod
    def status_line_renderable(
        cls,
        renderable: Text,
        *,
        prefix_style: str = "bold #46545C"
    ) -> Text:
        out = Text()
        prefix = cls.status_line_prefix()
        if prefix:
            out.append(prefix, style=prefix_style)
        out.append_text(renderable)
        return out

    @staticmethod
    def _status_shell_char(
        level: float,
        *,
        solid: str = "o",
        soft: str = ".",
        idle: str = " ",
        solid_threshold: float = 0.90,
        soft_threshold: float = 0.74
    ) -> str:
        if level > solid_threshold:
            return solid
        if level > soft_threshold:
            return soft
        return idle

    @staticmethod
    def _status_core_char(
        level: float,
        *,
        peak: str = "*",
        high: str = "O",
        mid: str = "o",
        low: str = ".",
        super_peak: str | None = None,
        super_threshold: float = 0.94,
        peak_threshold: float = 0.80,
        high_threshold: float = 0.62,
        mid_threshold: float = 0.46
    ) -> str:
        if super_peak is not None and level > super_threshold:
            return super_peak
        if level > peak_threshold:
            return peak
        if level > high_threshold:
            return high
        if level > mid_threshold:
            return mid
        return low

    @staticmethod
    def _status_edge_pair(
        level: float,
        *,
        threshold: float = 0.84,
        active_left: str = "<",
        active_right: str = ">",
        idle_left: str = "[",
        idle_right: str = "]"
    ) -> tuple[str, str]:
        if level > threshold:
            return active_left, active_right
        return idle_left, idle_right

    @classmethod
    def _build_status_shell(
        cls,
        *,
        edge_left: str,
        edge_right: str,
        left_outer: str,
        left_inner: str,
        core: str,
        right_inner: str,
        right_outer: str,
        edge_style: str,
        outer_style: str,
        inner_style: str,
        core_style: str
    ) -> Text:
        out = Text()
        out.append(edge_left, style=edge_style)
        out.append(left_outer, style=outer_style if left_outer.strip() else edge_style)
        out.append(left_inner, style=inner_style if left_inner.strip() else edge_style)
        out.append(cls.status_content_gap(), style=edge_style)
        out.append(core, style=core_style)
        out.append(cls.status_content_gap(), style=edge_style)
        out.append(right_inner, style=inner_style if right_inner.strip() else edge_style)
        out.append(right_outer, style=outer_style if right_outer.strip() else edge_style)
        out.append(edge_right, style=edge_style)
        return out

    @classmethod
    def _sway_focus(
        cls,
        phase: float,
        span: int,
        *,
        speed: float,
        pad: float = 0.0
    ) -> float:
        travel = max(1.0, float(span - 1) + (pad * 2.0))
        progress = 0.5 - (0.5 * math.cos(phase * speed))
        return (progress * travel) - pad

    @classmethod
    def _append_progressive_text(
        cls,
        out: Text,
        text: str,
        *,
        head: float,
        tail: float,
        peak_style: str,
        near_style: str,
        tail_style: str,
        dim_style: str,
        lead_glow: float = 0.4,
        tail_glow: float = 1.2
    ) -> None:
        for pos, char in enumerate(text):
            if char == " ":
                out.append(char, style=dim_style)
                continue

            if pos > head:
                style = near_style if (pos - head) <= lead_glow else dim_style
                out.append(char, style=style)
                continue

            if pos < tail:
                style = tail_style if (tail - pos) <= tail_glow else dim_style
                out.append(char, style=style)
                continue

            out.append(char, style=peak_style)

    @classmethod
    def _append_repair_text(
        cls,
        out: Text,
        text: str,
        *,
        left_focus: float,
        right_focus: float,
        peak_style: str,
        soft_style: str,
        near_style: str,
        mid_style: str,
        fade_style: str,
        dim_style: str,
        near_span: float,
        fade_span: float,
        peak_radius: float,
        lock_radius: float
    ) -> None:
        center = (max(0, len(text) - 1)) / 2.0
        focus_gap = abs(right_focus - left_focus)

        for pos, char in enumerate(text):
            left_distance = abs(pos - left_focus)
            right_distance = abs(pos - right_focus)
            distance = min(left_distance, right_distance)
            center_distance = abs(pos - center)

            if distance <= peak_radius:
                style = peak_style
            elif focus_gap <= lock_radius and center_distance <= max(1.0, peak_radius + 0.4):
                style = soft_style
            elif distance <= peak_radius + max(0.8, near_span * 0.22):
                style = soft_style
            elif distance <= peak_radius + max(1.4, near_span * 0.56):
                style = near_style
            elif distance <= peak_radius + max(2.1, fade_span * 0.58):
                style = mid_style
            elif distance <= peak_radius + max(3.0, fade_span):
                style = fade_style
            else:
                style = dim_style

            out.append(char, style=style)

    @classmethod
    def status_refresh_per_second(cls, kind: str) -> int:
        return int(cls.status_spec(kind).refresh_per_second)

    @classmethod
    def status_interval(cls, kind: str) -> float:
        return 1 / cls.status_refresh_per_second(kind)

    @classmethod
    def status_phase_rate(cls, kind: str) -> float:
        return float(cls.status_spec(kind).phase_rate)

    @classmethod
    def status_step(cls, kind: str) -> float:
        return cls.status_phase_rate(kind)

    @classmethod
    def _tool_status_indicator(
        cls,
        phase: float,
        *,
        subtle: bool = False,
        breathe_freq: float = 0.52,
        frame_rate: float = 0.72
    ) -> Text:
        colors = {
            "edge": "bold #6A6256",
            "shell": "bold #C7B8A1",
            "core": "bold #FFF7E7",
            "pulse": "bold #F2DEC0",
            "dust": "bold #8F816E"
        }
        if subtle:
            breathe = 0.46
            left_phase = 0.58
            right_phase = 0.58
            shell_phase = 0.0
        else:
            breathe = 0.5 + (0.5 * math.sin(phase * breathe_freq))
            left_phase = 0.5 + (0.5 * math.sin((phase * breathe_freq) - 0.95))
            right_phase = 0.5 + (0.5 * math.sin((phase * breathe_freq) + 0.95))
            shell_phase = phase * frame_rate

        shell_frames = (
            ("(", ")"),
            ("(", ")"),
            ("(", ")"),
            ("<", ">"),
            ("(", ")"),
            ("{", "}"),
            ("(", ")"),
            ("<", ">"),
            ("(", ")"),
        )
        shell_left, shell_right = ("(", ")") if subtle else shell_frames[
            int(shell_phase) % len(shell_frames)
        ]

        if left_phase > 0.92:
            chamber_left = "o"
        elif left_phase > 0.82:
            chamber_left = "."
        else:
            chamber_left = " "

        if right_phase > 0.92:
            chamber_right = "o"
        elif right_phase > 0.82:
            chamber_right = "."
        else:
            chamber_right = " "

        if subtle:
            core = "o"
            echo_left = " "
            echo_right = " "
        else:
            core = cls._status_core_char(
                breathe,
                super_peak="@",
                peak="*",
                high="O",
                mid="o",
                low="."
            )

            if breathe > 0.91:
                echo_left = "."
                echo_right = "."
            else:
                echo_left = " "
                echo_right = " "
        out = Text()

        out.append("[", style=colors["edge"])
        out.append(echo_left, style=colors["dust"] if echo_left.strip() else colors["edge"])
        out.append(chamber_left, style=colors["dust"])
        out.append(shell_left, style=colors["shell"] if breathe < 0.84 else colors["pulse"])
        out.append(core, style=colors["core"] if breathe > 0.64 else colors["pulse"])
        out.append(shell_right, style=colors["shell"] if breathe < 0.84 else colors["pulse"])
        out.append(chamber_right, style=colors["dust"])
        out.append(echo_right, style=colors["dust"] if echo_right.strip() else colors["edge"])
        out.append("]", style=colors["edge"])
        return out

    @classmethod
    def _heal_status_indicator(
        cls,
        phase: float,
        *,
        pulse_freq: float = 0.84,
        frame_rate: float = 0.38
    ) -> Text:
        colors = {
            "edge": "bold #3C535B",
            "shell": "bold #58C2CF",
            "pulse": "bold #8CEAF4",
            "core": "bold #E2FCFF",
            "core_lock": "bold #F4FFFF",
            "dust": "bold #486A71"
        }
        repair = 0.5 + (0.5 * math.sin(phase * pulse_freq))
        lock = cls._smoothstep(repair)
        shell_phase = phase * frame_rate

        shell_frames = (
            ("╱", "╲"),
            ("╱", "╲"),
            ("⟋", "⟍"),
            ("(", ")"),
            ("{", "}"),
            ("(", ")"),
            ("⟋", "⟍"),
        )
        shell_left, shell_right = shell_frames[int(shell_phase) % len(shell_frames)]

        if lock > 0.86:
            inner_left = "─"
            inner_right = "─"
            center = "◉"
        elif lock > 0.72:
            inner_left = "╲"
            inner_right = "╱"
            center = "·"
        elif lock > 0.56:
            inner_left = "╲"
            inner_right = "╱"
            center = "╳"
        elif lock > 0.38:
            inner_left = "."
            inner_right = "."
            center = "·"
        else:
            inner_left = " "
            inner_right = " "
            center = " "

        if lock > 0.90:
            echo_left = "·"
            echo_right = "·"
        elif lock > 0.68:
            echo_left = "."
            echo_right = "."
        else:
            echo_left = " "
            echo_right = " "

        out = Text()
        out.append("[", style=colors["edge"])
        out.append(echo_left, style=colors["dust"] if echo_left.strip() else colors["edge"])
        out.append(shell_left, style=colors["pulse"] if lock > 0.80 else colors["shell"])
        out.append(inner_left, style=colors["shell"] if inner_left.strip() else colors["edge"])
        out.append(
            center,
            style=colors["core_lock"] if lock > 0.88 else (colors["core"] if lock > 0.74 else colors["pulse"])
        )
        out.append(inner_right, style=colors["shell"] if inner_right.strip() else colors["edge"])
        out.append(shell_right, style=colors["pulse"] if lock > 0.80 else colors["shell"])
        out.append(echo_right, style=colors["dust"] if echo_right.strip() else colors["edge"])
        out.append("]", style=colors["edge"])
        return out


if __name__ == '__main__':
    pass
