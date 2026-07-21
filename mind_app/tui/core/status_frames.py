# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from .models import FormattedText

StatusFamily = typing.Literal["tool", "wait"]

ColorStop = tuple[float, str]

SWEEP_REFRESH_PER_SECOND = 30
SWEEP_ENTRY_PAD          = 1.2
SWEEP_PEAK_RADIUS        = 0.58
SWEEP_LEAD_SPAN          = 1.25
SWEEP_TAIL_SPAN          = 5.2
SWEEP_MIN_DURATION       = 1.75
SWEEP_MAX_DURATION       = 2.65
SPINNER_REFRESH_PER_SECOND = 10
SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


@dataclass(frozen=True, slots=True)
class SweepProfile(object):
    """描述状态族的字符、配色和相对扫光速度。"""

    dim_glyph: str
    peak_glyph: str
    speed_factor: float
    breathe_rate: float
    indicator_dim: str
    indicator_peak: str
    color_stops: tuple[ColorStop, ...]


SWEEP_PROFILES: dict[StatusFamily, SweepProfile] = {
    "tool": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=1.08,
        breathe_rate=4.7,
        indicator_dim="#5A4B42",
        indicator_peak="#DCC8AB",
        color_stops=(
            (0.00, "#5A4B42"),
            (0.18, "#755F4E"),
            (0.44, "#A48662"),
            (0.72, "#D8B77F"),
            (0.90, "#F6DCA8"),
            (1.00, "#FFF4D8"),
        ),
    ),
    "wait": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=0.92,
        breathe_rate=3.9,
        indicator_dim="#465652",
        indicator_peak="#B5CAC4",
        color_stops=(
            (0.00, "#465652"),
            (0.18, "#667873"),
            (0.44, "#7C8F89"),
            (0.72, "#A4B5B0"),
            (0.90, "#C5D4CF"),
            (1.00, "#DDE7E3"),
        ),
    ),
}


def status_interval(family: StatusFamily) -> float:
    """返回状态族的统一刷新间隔。"""
    _profile(family)
    return 1 / SWEEP_REFRESH_PER_SECOND


def status_phase_rate(family: StatusFamily) -> float:
    """返回以真实秒数推进的动画相位速率。"""
    _profile(family)
    return 1.0


def render_status_fragments(
    text: str,
    *,
    family: StatusFamily,
    phase: float,
    animated: bool,
) -> FormattedText:
    """生成带固定指示符和连续扫光的状态片段。"""
    profile = _profile(family)

    out = [
        status_indicator_fragment(
            phase,
            family=family,
            animated=animated,
        ),
        (_style(profile.color_stops[0][1]), " "),
    ]

    if not animated:
        out.append((_style(_gradient_color(profile.color_stops, 0.82)), text))
        return out

    out.extend(_sweep_fragments(text, phase=phase, profile=profile))
    return out


def status_indicator_fragment(
    phase: float,
    *,
    family: StatusFamily,
    animated: bool,
) -> tuple[str, str]:
    """生成宽度固定的状态指示符。"""
    profile = _profile(family)

    if not animated:
        color = _mix_hex_color(
            profile.indicator_dim,
            profile.indicator_peak,
            0.72,
        )
        return _style(color), profile.peak_glyph

    breathe = 0.5 + (0.5 * math.sin(float(phase) * profile.breathe_rate))

    color = _mix_hex_color(
        profile.indicator_dim,
        profile.indicator_peak,
        _smoothstep(breathe) * 0.72,
    )
    glyph = profile.peak_glyph if breathe >= 0.5 else profile.dim_glyph
    return _style(color), glyph


def spinner_indicator_fragment(
    phase: float,
    *,
    family: StatusFamily = "wait",
) -> tuple[str, str]:
    """生成显式长任务使用的单字符旋转指示符。"""
    style, _glyph = status_indicator_fragment(
        phase,
        family=family,
        animated=True,
    )
    frame = SPINNER_FRAMES[
        int(max(0.0, float(phase)) * SPINNER_REFRESH_PER_SECOND)
        % len(SPINNER_FRAMES)
    ]
    return style, frame


def _sweep_fragments(
    text: str,
    *,
    phase: float,
    profile: SweepProfile,
) -> FormattedText:
    """按字符到光头的距离生成连续渐变光带。"""
    cells     = _character_cells(text)
    span      = max(1, _display_span(cells))
    focus     = _sweep_focus(float(phase), span=span, profile=profile)
    dim_color = profile.color_stops[0][1]

    out: FormattedText = []

    for position, char in cells:
        if char.isspace():
            out.append((_style(dim_color), char))
            continue

        intensity = _sweep_intensity(position, focus=focus)
        color = _gradient_color(profile.color_stops, intensity)
        out.append((_style(color), char))

    return out


def _sweep_focus(
    elapsed: float,
    *,
    span: int,
    profile: SweepProfile,
) -> float:
    """按文本宽度和真实时间计算循环光头位置。"""
    width    = max(1, int(span))
    exit_pad = SWEEP_PEAK_RADIUS + SWEEP_TAIL_SPAN

    travel = max(
        1.0,
        float(max(0, width - 1)) + SWEEP_ENTRY_PAD + exit_pad,
    )

    speed = travel / _sweep_duration(width) * profile.speed_factor
    return ((max(0.0, float(elapsed)) * speed) % travel) - SWEEP_ENTRY_PAD


def _sweep_duration(span: int) -> float:
    """返回随文本宽度温和增长的基础扫光周期。"""
    width = max(1, int(span))

    return max(
        SWEEP_MIN_DURATION,
        min(SWEEP_MAX_DURATION, 1.55 + (width * 0.035)),
    )


def _sweep_intensity(position: float, *, focus: float) -> float:
    """计算带短前沿和长尾迹的连续扫光强度。"""
    delta = float(position) - float(focus)

    distance = abs(delta)
    if distance <= SWEEP_PEAK_RADIUS:
        core = distance / max(0.001, SWEEP_PEAK_RADIUS)
        return 1.0 - (0.06 * _smoothstep(core))

    span       = SWEEP_LEAD_SPAN if delta >= 0.0 else SWEEP_TAIL_SPAN
    normalized = (distance - SWEEP_PEAK_RADIUS) / max(0.001, span)
    intensity  = 0.94 * (1.0 - _smoothstep(normalized))

    return max(0.0, min(1.0, intensity))


def _gradient_color(stops: tuple[ColorStop, ...], intensity: float) -> str:
    """在颜色停靠点之间插值生成当前强度颜色。"""
    level = max(0.0, min(1.0, float(intensity)))
    if level <= stops[0][0]:
        return stops[0][1]

    for index in range(1, len(stops)):
        end_level, end_color     = stops[index]
        start_level, start_color = stops[index - 1]

        if level <= end_level:
            span  = max(0.0001, end_level - start_level)
            local = _smoothstep((level - start_level) / span)

            return _mix_hex_color(start_color, end_color, local)

    return stops[-1][1]


def _character_cells(text: str) -> list[tuple[float, str]]:
    """返回每个字符在终端显示列中的中心位置。"""
    cursor = 0

    cells: list[tuple[float, str]] = []

    for char in str(text):
        width = max(0, get_cwidth(char))
        cells.append((cursor + (max(1, width) - 1) / 2, char))
        cursor += width

    return cells


def _display_span(cells: list[tuple[float, str]]) -> int:
    """计算字符位置序列占用的终端显示列数。"""
    if not cells:
        return 0
    position, char = cells[-1]
    return max(1, round(position + ((max(1, get_cwidth(char)) + 1) / 2)))


def _mix_hex_color(start: str, end: str, weight: float) -> str:
    """按给定权重混合两个十六进制颜色。"""
    ratio = max(0.0, min(1.0, float(weight)))
    left  = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
    right = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(round(a + ((b - a) * ratio)) for a, b in zip(left, right))

    return "#" + "".join(f"{value:02X}" for value in mixed)


def _smoothstep(value: float) -> float:
    """把线性输入转换为平滑过渡权重。"""
    clamped = max(0.0, min(1.0, float(value)))
    return clamped * clamped * (3.0 - (2.0 * clamped))


def _style(color: str) -> str:
    """生成 prompt_toolkit 使用的前景样式。"""
    return f"fg:{color}"


def _profile(family: StatusFamily) -> SweepProfile:
    """返回状态族对应的扫光配置。"""
    return SWEEP_PROFILES[family]


if __name__ == '__main__':
    pass
