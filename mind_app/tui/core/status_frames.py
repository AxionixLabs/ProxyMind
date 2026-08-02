# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from .models import FormattedText

StatusFamily = typing.Literal["tool", "wait"]

SWEEP_TAIL_SPAN    = 7.2
SWEEP_MIN_DURATION = 1.08
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
    lead_span: float
    tail_span: float
    breathe_rate: float
    palette_period: float
    palettes: tuple[SweepPalette, ...]


SWEEP_PROFILES: dict[StatusFamily, SweepProfile] = {
    "tool": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=1.12,
        rest_duration=1.25,
        lead_span=2.35,
        tail_span=7.4,
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
        speed_factor=0.96,
        rest_duration=1.65,
        lead_span=2.7,
        tail_span=8.6,
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
) -> FormattedText:
    """按点亮头部和收尾边界生成累积扫光。"""
    cells = _character_cells(text)
    span  = max(1, _display_span(cells))

    head, tail = _sweep_boundaries(float(phase), span=span, profile=profile)

    dim_color = palette.color_stops[0][1]

    out: FormattedText = []

    for position, char in cells:
        if char.isspace():
            out.append((_style(dim_color), char))
            continue

        intensity = _sweep_intensity(
            position,
            head=head,
            tail=tail,
            lead_span=profile.lead_span,
            tail_span=profile.tail_span,
        )

        color = _gradient_color(palette.color_stops, intensity)
        out.append((_style(color), char))

    return out


def _sweep_boundaries(
    elapsed: float,
    *,
    span: int,
    profile: SweepProfile
) -> tuple[float, float]:
    """按连续点亮、收尾和静默阶段计算前后边界。"""
    width         = max(1, int(span))
    last_position = float(max(0, width - 1))

    fill_duration, tail_duration = _sweep_durations(width, profile)

    active_duration = fill_duration + tail_duration
    cycle_duration  = active_duration + profile.rest_duration
    cycle_elapsed   = max(0.0, float(elapsed)) % cycle_duration

    head_start = -profile.lead_span
    head_end   = last_position
    tail_start = 0.0
    tail_end   = last_position + profile.tail_span

    if cycle_elapsed < fill_duration:
        progress = cycle_elapsed / max(0.001, fill_duration)
        head     = head_start + ((head_end - head_start) * progress)

        return head, tail_start

    if cycle_elapsed < active_duration:
        tail_elapsed = cycle_elapsed - fill_duration
        progress     = tail_elapsed / max(0.001, tail_duration)
        tail         = tail_start + ((tail_end - tail_start) * progress)

        return head_end, tail

    return head_end, tail_end


def _sweep_durations(
    span: int,
    profile: SweepProfile,
) -> tuple[float, float]:
    """以相同边界速度计算点亮和收尾阶段时长。"""
    width         = max(1, int(span))
    last_position = float(max(0, width - 1))
    fill_duration = _sweep_duration(width) / profile.speed_factor
    fill_travel   = last_position + profile.lead_span
    tail_travel   = last_position + profile.tail_span

    tail_duration = fill_duration * (
        tail_travel / max(0.001, fill_travel)
    )
    return fill_duration, tail_duration


def _sweep_intensity(
    position: float,
    *,
    head: float,
    tail: float,
    lead_span: float = 2.35,
    tail_span: float = SWEEP_TAIL_SPAN
) -> float:
    """先累积点亮全部字符，再从左向右渐进收尾。"""
    location = float(position)

    if location > head:
        distance = (location - head) / max(0.001, lead_span)
        return 1.0 - _smoothstep(distance)

    if location < tail:
        distance = (tail - location) / max(0.001, tail_span)
        return 1.0 - _smoothstep(distance)

    return 1.0


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


def _profile(family: StatusFamily) -> SweepProfile:
    """返回状态族对应的扫光配置。"""
    return SWEEP_PROFILES[family]


if __name__ == '__main__':
    pass
