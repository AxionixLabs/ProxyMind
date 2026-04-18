# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
from rich.text import Text
from rich.cells import cell_len
from .types import AgentLiveTheme
from ..utils import (
    ease_in_out_sine, mix_hex_color
)


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
    sweep_echo    = 0.5 + 0.5 * math.sin(phase * 1.72 + 2.1)
    title_glow    = 0.48 + breathe * 0.24
    detail_glow   = 0.18 + breathe * 0.18
    pulse_glow    = 0.30 + breathe * 0.30
    title_tone    = ease_in_out_sine(title_glow)
    detail_tone   = ease_in_out_sine(detail_glow)
    pulse_tone    = ease_in_out_sine(pulse_glow)
    pulse_frames  = ("⠈", "⠐", "⠠", "⢀", "⡀", "⠄", "⠂", "⠁", "⠂", "⠄", "⡀", "⢀", "⠠", "⠐")
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


if __name__ == '__main__':
    pass
