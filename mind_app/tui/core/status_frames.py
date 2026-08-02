# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from mind_core.design.terminal_capabilities import TerminalColorLevel
from .models import FormattedText

StatusFamily = typing.Literal["tool", "wait"]

SWEEP_GLOW_SPAN    = 2.8
SWEEP_MIN_DURATION = 1.22
SWEEP_MAX_DURATION = 1.48

SWEEP_REFRESH_PER_SECOND   = 30
SPINNER_REFRESH_PER_SECOND = 10

SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


@dataclass(frozen=True, slots=True)
class SweepPalette(object):
    """描述状态动画的一组连续渐变颜色。"""
    indicator_dim: str
    indicator_peak: str
    color_stops: tuple[tuple[float, str], ...]


@dataclass(frozen=True, slots=True)
class SweepProfile(object):
    """描述状态族的字符、节奏和动态配色。"""
    dim_glyph: str
    peak_glyph: str
    speed_factor: float
    rest_duration: float
    peak_radius: float
    glow_span: float
    breathe_rate: float
    palette_period: float
    palettes: tuple[SweepPalette, ...]


SWEEP_PROFILES: dict[StatusFamily, SweepProfile] = {
    "tool": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=1.05,
        rest_duration=0.58,
        peak_radius=0.72,
        glow_span=2.65,
        breathe_rate=4.4,
        palette_period=8.5,
        palettes=(
            SweepPalette(
                indicator_dim="#594D45",
                indicator_peak="#E8CF9B",
                color_stops=(
                    (0.00, "#69584F"),
                    (0.18, "#806D5E"),
                    (0.44, "#A98867"),
                    (0.72, "#DDBA79"),
                    (0.90, "#F6D99C"),
                    (1.00, "#FFF0C2"),
                ),
            ),
            SweepPalette(
                indicator_dim="#5E4A47",
                indicator_peak="#EDBFA5",
                color_stops=(
                    (0.00, "#6D5651"),
                    (0.18, "#87665D"),
                    (0.44, "#AF7B66"),
                    (0.72, "#E4A477"),
                    (0.90, "#F8C69E"),
                    (1.00, "#FFE0C2"),
                ),
            ),
            SweepPalette(
                indicator_dim="#4F5850",
                indicator_peak="#CED89D",
                color_stops=(
                    (0.00, "#5A635A"),
                    (0.18, "#6D786A"),
                    (0.44, "#8C9573"),
                    (0.72, "#C0BC78"),
                    (0.90, "#E6D69A"),
                    (1.00, "#F7EAB9"),
                ),
            ),
        ),
    ),
    "wait": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=0.94,
        rest_duration=0.68,
        peak_radius=0.80,
        glow_span=2.90,
        breathe_rate=3.6,
        palette_period=10.5,
        palettes=(
            SweepPalette(
                indicator_dim="#405552",
                indicator_peak="#B9DDD2",
                color_stops=(
                    (0.00, "#536864"),
                    (0.18, "#627A76"),
                    (0.44, "#78948D"),
                    (0.72, "#A8C8BF"),
                    (0.90, "#CAE4DC"),
                    (1.00, "#E5F6F0"),
                ),
            ),
            SweepPalette(
                indicator_dim="#405162",
                indicator_peak="#B9D5EC",
                color_stops=(
                    (0.00, "#516372"),
                    (0.18, "#5F7688"),
                    (0.44, "#7490A8"),
                    (0.72, "#A5C3DA"),
                    (0.90, "#C8DDF0"),
                    (1.00, "#E6F1FC"),
                ),
            ),
            SweepPalette(
                indicator_dim="#46565B",
                indicator_peak="#BEDCD9",
                color_stops=(
                    (0.00, "#58696C"),
                    (0.18, "#657C7E"),
                    (0.44, "#7D9697"),
                    (0.72, "#ACC8C7"),
                    (0.90, "#CEE2E0"),
                    (1.00, "#E8F3F1"),
                ),
            ),
        ),
    ),
}


def render_status_fragments(
    text: str,
    *,
    family: StatusFamily,
    phase: float,
    animated: bool,
    color_level: TerminalColorLevel = TerminalColorLevel.UNKNOWN
) -> FormattedText:
    """生成带固定指示符和连续扫光的状态片段。"""
    profile = _profile(family)
    palette = _animated_palette(profile, phase if animated else 0.0)

    out = [
        status_indicator_fragment(
            phase,
            family=family,
            animated=animated,
        ),
        (_style(palette.color_stops[0][1]), " "),
    ]

    if not animated:
        out.append((_style(_gradient_color(palette.color_stops, 0.82)), text))
        return out

    out.extend(_sweep_fragments(
        text,
        phase=phase,
        profile=profile,
        palette=palette,
        color_level=color_level,
    ))
    return out


def status_interval(family: StatusFamily) -> float:
    """返回状态族的统一刷新间隔。"""
    _profile(family)
    return 1 / SWEEP_REFRESH_PER_SECOND


def status_phase_rate(family: StatusFamily) -> float:
    """返回以真实秒数推进的动画相位速率。"""
    _profile(family)
    return 1.0


def status_indicator_fragment(
    phase: float,
    *,
    family: StatusFamily,
    animated: bool,
) -> tuple[str, str]:
    """生成宽度固定的状态指示符。"""
    profile = _profile(family)
    palette = _animated_palette(profile, phase if animated else 0.0)

    if not animated:
        color = _mix_hex_color(
            palette.indicator_dim,
            palette.indicator_peak,
            0.72,
        )
        return _style(color), profile.peak_glyph

    breathe = 0.5 + (0.5 * math.sin(float(phase) * profile.breathe_rate))

    color = _mix_hex_color(
        palette.indicator_dim,
        palette.indicator_peak,
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


def _sweep_duration(span: int) -> float:
    """返回随文本宽度温和增长的单次扫光时长。"""
    width = max(1, int(span))

    return max(
        SWEEP_MIN_DURATION,
        min(SWEEP_MAX_DURATION, 0.96 + (width * 0.019)),
    )


def _sweep_fragments(
    text: str,
    *,
    phase: float,
    profile: SweepProfile,
    palette: SweepPalette,
    color_level: TerminalColorLevel
) -> FormattedText:
    """按字符到光带中心的距离生成局部扫光。"""
    cells     = _character_cells(text)
    span      = max(1, _display_span(cells))
    focus     = _sweep_focus(float(phase), span=span, profile=profile)
    dim_color = palette.color_stops[0][1]

    out: FormattedText = []

    for position, char in cells:
        if char.isspace():
            out.append((_sweep_style(
                dim_color,
                intensity=0.0,
                color_level=color_level,
            ), char))
            continue

        intensity = _sweep_intensity(
            position,
            focus=focus,
            peak_radius=profile.peak_radius,
            glow_span=profile.glow_span,
        )

        color = _gradient_color(palette.color_stops, intensity)

        out.append((_sweep_style(
            color,
            intensity=intensity,
            color_level=color_level,
        ), char))

    return out


def _sweep_focus(
    elapsed: float,
    *,
    span: int,
    profile: SweepProfile
) -> float:
    """按真实时间计算带静默间隔的局部光带中心。"""
    width           = max(1, int(span))
    last_position   = float(max(0, width - 1))
    band_extent     = profile.peak_radius + profile.glow_span
    active_duration = _sweep_duration(width) / profile.speed_factor
    cycle_duration  = active_duration + profile.rest_duration
    cycle_elapsed   = max(0.0, float(elapsed)) % cycle_duration

    if cycle_elapsed >= active_duration:
        return last_position + band_extent

    progress = cycle_elapsed / max(0.001, active_duration)
    travel   = last_position + (band_extent * 2.0)

    return -band_extent + (travel * progress)


def _sweep_intensity(
    position: float,
    *,
    focus: float,
    peak_radius: float = 0.76,
    glow_span: float = SWEEP_GLOW_SPAN,
) -> float:
    """计算具有柔和中心和对称衰减的局部光带强度。"""
    distance = abs(float(position) - float(focus))

    if distance <= peak_radius:
        core = distance / max(0.001, peak_radius)
        return 1.0 - (0.05 * _smoothstep(core))

    normalized = (distance - peak_radius) / max(0.001, glow_span)
    intensity  = 0.95 * (1.0 - _smoothstep(normalized))

    return max(0.0, min(1.0, intensity))


def _animated_palette(profile: SweepProfile, elapsed: float) -> SweepPalette:
    """在同一状态族的配色之间缓慢循环插值。"""
    palettes = profile.palettes
    if len(palettes) == 1:
        return palettes[0]

    position = max(0.0, float(elapsed)) / max(0.001, profile.palette_period)
    index    = int(position) % len(palettes)
    blend    = _smoothstep(position - int(position))

    start = palettes[index]
    end   = palettes[(index + 1) % len(palettes)]

    color_stops = tuple(
        (
            start_level,
            _mix_hex_color(start_color, end_color, blend),
        )
        for (start_level, start_color), (_end_level, end_color)
        in zip(start.color_stops, end.color_stops)
    )

    return SweepPalette(
        indicator_dim=_mix_hex_color(
            start.indicator_dim,
            end.indicator_dim,
            blend,
        ),
        indicator_peak=_mix_hex_color(
            start.indicator_peak,
            end.indicator_peak,
            blend,
        ),
        color_stops=color_stops,
    )


def _gradient_color(stops: tuple[tuple[float, str], ...], intensity: float) -> str:
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
    cursor: int = 0

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


def _sweep_style(
    color: str,
    *,
    intensity: float,
    color_level: TerminalColorLevel,
) -> str:
    """根据终端色深生成扫光字符的颜色和字重。"""
    if color_level == TerminalColorLevel.TRUECOLOR:
        return f"bold {_style(color)}"
    if intensity < 0.2:
        return f"dim {_style(color)}"
    if intensity > 0.6:
        return f"bold {_style(color)}"

    return _style(color)


def _profile(family: StatusFamily) -> SweepProfile:
    """返回状态族对应的扫光配置。"""
    return SWEEP_PROFILES[family]


if __name__ == '__main__':
    pass
