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

SWEEP_PADDING = 10
SWEEP_PERIOD_SECONDS = 2.0
SWEEP_BAND_HALF_WIDTH = 5.0
SWEEP_FRAME_INTERVAL_SECONDS = 0.032
INDICATOR_BLINK_INTERVAL_SECONDS = 0.6
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
    """描述状态族活动指示符使用的字符。"""

    dim_glyph: str
    peak_glyph: str


SWEEP_PROFILES: dict[StatusFamily, SweepProfile] = {
    "tool": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
    ),
    "wait": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
    ),
    "retry": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
    ),
    "provider_retry": SweepProfile(
        dim_glyph="◦",
        peak_glyph="•",
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
        base_style=base_style,
    ))
    return out


def status_interval(family: StatusFamily) -> float:
    """返回状态族的统一刷新间隔。"""
    _profile(family)
    return SWEEP_FRAME_INTERVAL_SECONDS


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

    blink_frame = int(
        max(0.0, float(phase)) / INDICATOR_BLINK_INTERVAL_SECONDS
    )
    bright = blink_frame % 2 == 0
    modifier = "bold" if bright else "dim"
    glyph = profile.peak_glyph if bright else profile.dim_glyph
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


def _sweep_fragments(
    text: str,
    *,
    phase: float,
    base_style: str,
) -> FormattedText:
    """按字符到光带中心的距离生成修饰符扫光。"""
    cells = _character_cells(text)
    span = max(1, _display_span(cells))
    focus = _sweep_focus(float(phase), span=span)
    out: FormattedText = []
    for position, char in cells:
        intensity = 0.0 if char.isspace() else _sweep_intensity(
            position,
            focus=focus,
        )
        out.append((_sweep_style(base_style, intensity=intensity), char))
    return out


def _sweep_focus(
    elapsed: float,
    *,
    span: int,
) -> float:
    """按固定周期计算包含首尾缓冲区的光带中心。"""
    width = max(1, int(span))
    period = width + (SWEEP_PADDING * 2)
    cycle_elapsed = max(0.0, float(elapsed)) % SWEEP_PERIOD_SECONDS
    position = int(
        (cycle_elapsed / SWEEP_PERIOD_SECONDS) * period
    )
    return float(position - SWEEP_PADDING)


def _sweep_intensity(
    position: float,
    *,
    focus: float,
) -> float:
    """使用余弦曲线计算固定宽度光带的对称强度。"""
    distance = abs(float(position) - float(focus))
    if distance > SWEEP_BAND_HALF_WIDTH:
        return 0.0
    angle = math.pi * (distance / SWEEP_BAND_HALF_WIDTH)
    return 0.5 * (1.0 + math.cos(angle))


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
