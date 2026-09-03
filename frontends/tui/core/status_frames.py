# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from dataclasses import dataclass

from prompt_toolkit.utils import get_cwidth

from frontends.terminal.color_support import TerminalColorLevel
from .models import FormattedText

StatusFamily = typing.Literal[
    "tool",
    "wait",
    "retry",
    "provider_retry"
]

SWEEP_GLOW_SPAN = 2.8
SWEEP_MIN_DURATION = 1.22
SWEEP_MAX_DURATION = 1.48
SWEEP_REFRESH_PER_SECOND = 30
SPINNER_REFRESH_PER_SECOND = 10
SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

_FAMILY_STYLE_CLASSES: dict[StatusFamily, str] = {
    "tool": "class:terminal.attention.plain",
    "wait": "class:terminal.accent",
    "retry": "class:terminal.attention.plain",
    "provider_retry": "class:terminal.brand",
}

@dataclass(frozen=True, slots=True)
class SweepProfile(object):
    """描述状态族的字符、节奏和扫光几何。"""

    dim_glyph: str
    peak_glyph: str
    speed_factor: float
    rest_duration: float
    peak_radius: float
    glow_span: float
    breathe_rate: float


SWEEP_PROFILES: dict[StatusFamily, SweepProfile] = {
    "tool": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=1.05,
        rest_duration=0.58,
        peak_radius=0.72,
        glow_span=2.65,
        breathe_rate=4.4,
    ),
    "wait": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=0.94,
        rest_duration=0.68,
        peak_radius=0.80,
        glow_span=2.90,
        breathe_rate=3.6,
    ),
    "retry": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=0.94,
        rest_duration=0.68,
        peak_radius=0.80,
        glow_span=2.90,
        breathe_rate=3.6,
    ),
    "provider_retry": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
        speed_factor=0.94,
        rest_duration=0.68,
        peak_radius=0.80,
        glow_span=2.90,
        breathe_rate=3.6,
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
    """生成使用指定动画族的状态片段。"""
    profile = _profile(family)
    base_style = _family_style(family, color_level=color_level)
    out = [
        status_indicator_fragment(
            phase,
            family=family,
            animated=animated,
            color_level=color_level,
        ),
        ("", " "),
    ]
    if not animated:
        out.append((base_style, text))
        return out

    out.extend(_sweep_fragments(
        text,
        phase=phase,
        profile=profile,
        base_style=base_style,
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
    color_level: TerminalColorLevel = TerminalColorLevel.UNKNOWN,
) -> tuple[str, str]:
    """生成宽度固定且使用状态语义色的指示符。"""
    profile = _profile(family)
    base_style = _family_style(family, color_level=color_level)
    if not animated:
        return _with_modifier(base_style, "bold"), profile.peak_glyph

    breathe = 0.5 + (0.5 * math.sin(float(phase) * profile.breathe_rate))
    modifier = "bold" if breathe >= 0.5 else "dim"
    glyph = profile.peak_glyph if breathe >= 0.5 else profile.dim_glyph
    return _with_modifier(base_style, modifier), glyph


def spinner_indicator_fragment(
    phase: float,
    *,
    family: StatusFamily = "wait",
    color_level: TerminalColorLevel = TerminalColorLevel.UNKNOWN,
) -> tuple[str, str]:
    """生成保持常规字重的单字符旋转指示符。"""
    style = _family_style(family, color_level=color_level)
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
    base_style: str,
) -> FormattedText:
    """按字符到光带中心的距离生成修饰符扫光。"""
    cells = _character_cells(text)
    span = max(1, _display_span(cells))
    focus = _sweep_focus(float(phase), span=span, profile=profile)
    out: FormattedText = []
    for position, char in cells:
        intensity = 0.0 if char.isspace() else _sweep_intensity(
            position,
            focus=focus,
            peak_radius=profile.peak_radius,
            glow_span=profile.glow_span,
        )
        out.append((_sweep_style(base_style, intensity=intensity), char))
    return out


def _sweep_focus(
    elapsed: float,
    *,
    span: int,
    profile: SweepProfile
) -> float:
    """按真实时间计算带静默间隔的局部光带中心。"""
    width = max(1, int(span))
    last_position = float(max(0, width - 1))
    band_extent = profile.peak_radius + profile.glow_span
    active_duration = _sweep_duration(width) / profile.speed_factor
    cycle_duration = active_duration + profile.rest_duration
    cycle_elapsed = max(0.0, float(elapsed)) % cycle_duration
    if cycle_elapsed >= active_duration:
        return last_position + band_extent

    progress = cycle_elapsed / max(0.001, active_duration)
    travel = last_position + (band_extent * 2.0)
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
    intensity = 0.95 * (1.0 - _smoothstep(normalized))
    return max(0.0, min(1.0, intensity))


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


def _smoothstep(value: float) -> float:
    """把线性输入转换为平滑过渡权重。"""
    clamped = max(0.0, min(1.0, float(value)))
    return clamped * clamped * (3.0 - (2.0 * clamped))


def _family_style(
    family: StatusFamily,
    *,
    color_level: TerminalColorLevel,
) -> str:
    """返回当前色深下的状态族语义样式类。"""
    _profile(family)
    if color_level is TerminalColorLevel.NONE:
        return ""
    return _FAMILY_STYLE_CLASSES[family]


def _with_modifier(style: str, modifier: str) -> str:
    """给可选语义样式附加单个修饰符。"""
    return f"{style} {modifier}".strip()


def _sweep_style(base_style: str, *, intensity: float) -> str:
    """根据扫光强度选择稳定的终端修饰符。"""
    if intensity < 0.2:
        return _with_modifier(base_style, "dim")
    if intensity > 0.6:
        return _with_modifier(base_style, "bold")
    return base_style


def _profile(family: StatusFamily) -> SweepProfile:
    """返回状态族对应的扫光配置。"""
    return SWEEP_PROFILES[family]


if __name__ == '__main__':
    pass
