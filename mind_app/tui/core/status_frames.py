# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math
import typing
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from .models import FormattedText

StatusFamily = typing.Literal["tool", "mode", "wait"]


@dataclass(frozen=True, slots=True)
class SweepFrameSpec(object):
    """描述 TUI 状态文字的扫光节奏。"""

    refresh_per_second: int
    phase_rate: float
    entry_pad: float
    exit_pad: float
    tail_span: float
    peak_radius: float


SWEEP_SPECS: dict[StatusFamily, SweepFrameSpec] = {
    "tool": SweepFrameSpec(30, 15.8, 1.2, 8.2, 6.8, 0.74),
    "mode": SweepFrameSpec(30, 13.6, 1.0, 3.0, 4.6, 0.52),
    "wait": SweepFrameSpec(30, 15.6, 1.2, 4.0, 5.2, 0.58),
}

STATUS_COLORS: dict[StatusFamily, dict[str, str]] = {
    "tool": {
        "indicator_dim": "#5A4B42",
        "indicator_peak": "#DCC8AB",
        "peak": "#FFF4D8",
        "soft": "#F6DCA8",
        "near": "#D8B77F",
        "mid": "#A48662",
        "fade": "#755F4E",
        "dim": "#5A4B42",
    },
    "mode": {
        "indicator_dim": "#2F4C5A",
        "indicator_peak": "#91D7ED",
        "peak": "#EAF9FF",
        "soft": "#C7EEF9",
        "near": "#91D7ED",
        "mid": "#4B8FA8",
        "fade": "#335F72",
        "dim": "#2F4C5A",
    },
    "wait": {
        "indicator_dim": "#465652",
        "indicator_peak": "#B5CAC4",
        "peak": "#DDE7E3",
        "soft": "#C5D4CF",
        "near": "#A4B5B0",
        "mid": "#7C8F89",
        "fade": "#667873",
        "dim": "#465652",
    },
}


def status_interval(family: StatusFamily) -> float:
    """返回状态族的帧刷新间隔。"""
    return 1 / SWEEP_SPECS[family].refresh_per_second


def status_phase_rate(family: StatusFamily) -> float:
    """返回状态族每秒推进的相位。"""
    return SWEEP_SPECS[family].phase_rate


def render_status_fragments(
    text: str,
    *,
    family: StatusFamily,
    phase: float,
    animated: bool,
) -> FormattedText:
    """生成带呼吸指示和逐字符扫光的状态片段。"""
    colors = STATUS_COLORS[family]

    out = [
        _indicator_fragment(phase, family=family, animated=animated),
        (_style(colors["dim"]), " "),
    ]

    if not animated:
        out.append((_style(colors["soft"]), text))
        return out

    if family == "wait":
        out.extend(_progressive_fragments(text, phase=phase, colors=colors))
    else:
        out.extend(_sweep_fragments(
            text,
            phase=phase,
            spec=SWEEP_SPECS[family],
            colors=colors,
        ))

    return out


def _indicator_fragment(
    phase: float,
    *,
    family: StatusFamily,
    animated: bool,
) -> tuple[str, str]:
    """生成与状态族配色一致的呼吸指示点。"""
    colors = STATUS_COLORS[family]
    if not animated:
        return _style(colors["soft"]), "•"

    frequency = {"tool": 0.30, "mode": 0.44, "wait": 0.25}[family]
    breathe   = 0.5 + (0.5 * math.sin(phase * frequency))
    weight    = _smoothstep(breathe) * 0.72

    color = _mix_hex_color(
        colors["indicator_dim"],
        colors["indicator_peak"],
        weight,
    )

    return _style(color), "•" if breathe > 0.58 else "◦"


def _sweep_fragments(
    text: str,
    *,
    phase: float,
    spec: SweepFrameSpec,
    colors: dict[str, str],
) -> FormattedText:
    """生成从左向右移动并带衰减尾迹的扫光文字。"""
    cells = _character_cells(text)

    focus = _drift_focus(
        phase,
        _display_span(cells),
        entry_pad=spec.entry_pad,
        exit_pad=spec.exit_pad,
    )

    out: FormattedText = []

    for position, char in cells:
        delta = focus - position
        if 0.0 <= delta <= spec.peak_radius:
            color = colors["peak"]
        elif delta > spec.peak_radius:
            tail = (delta - spec.peak_radius) / max(0.001, spec.tail_span)
            if tail <= 0.14:
                color = colors["soft"]
            elif tail <= 0.34:
                color = colors["near"]
            elif tail <= 0.62:
                color = colors["mid"]
            elif tail <= 1.0:
                color = colors["fade"]
            else:
                color = colors["dim"]
        else:
            color = colors["dim"]
        out.append((_style(color), char))

    return out


def _progressive_fragments(
    text: str,
    *,
    phase: float,
    colors: dict[str, str],
) -> FormattedText:
    """生成等待状态循环前进的渐进光带。"""
    cells = _character_cells(text)
    span  = max(1, _display_span(cells))
    head  = ((phase * 0.78) % max(1.0, float((span * 2) + 4.0))) - 1.2
    tail  = -1.6 if head < float(span - 1) else head - max(0.0, float(span - 1))

    out: FormattedText = []

    for position, char in cells:
        if char == " ":
            color = colors["dim"]
        elif position > head:
            color = colors["near"] if position - head <= 0.36 else colors["dim"]
        elif position < tail:
            color = colors["fade"] if tail - position <= 1.0 else colors["dim"]
        else:
            color = colors["peak"]
        out.append((_style(color), char))

    return out


def _drift_focus(
    phase: float,
    span: int,
    *,
    entry_pad: float,
    exit_pad: float,
) -> float:
    """计算包含进出留白的循环扫光焦点。"""
    travel = max(1.0, float(max(0, span - 1)) + entry_pad + exit_pad)
    return (phase % travel) - entry_pad


def _character_cells(text: str) -> list[tuple[float, str]]:
    """返回每个字符在终端显示列中的中心位置。"""
    cursor: int = 0

    cells: list[tuple[float, str]] = []

    for char in text:
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
    """把线性相位转换为平滑过渡权重。"""
    clamped = max(0.0, min(1.0, float(value)))
    return clamped * clamped * (3.0 - (2.0 * clamped))


def _style(color: str) -> str:
    """生成 prompt_toolkit 使用的强调前景样式。"""
    return f"fg:{color} bold"


if __name__ == '__main__':
    pass
