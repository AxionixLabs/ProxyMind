# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import math

from .capabilities import (
    RgbColor,
    TerminalColorLevel,
)

__all__ = [
    "best_color",
    "blend_color",
    "is_light_color",
    "selection_color",
    "semantic_color",
]

_DARK_SELECTION_RGB: RgbColor = (91, 141, 239)
_LIGHT_SELECTION_RGB: RgbColor = (0, 95, 135)


def best_color(
    color: RgbColor,
    level: TerminalColorLevel
) -> str | None:
    """按输出色阶选择最接近的 prompt_toolkit 颜色表示。"""
    if level == TerminalColorLevel.TRUECOLOR:
        return _hex(color)
    if level == TerminalColorLevel.ANSI256:
        return _hex(_xterm_palette()[_nearest_xterm_index(color)])
    return None


def semantic_color(
    color: RgbColor,
    level: TerminalColorLevel,
    *,
    fallback: str
) -> str:
    """返回可直接用于 prompt_toolkit 的语义颜色。"""
    return best_color(color, level) or fallback


def selection_color(
    level: TerminalColorLevel,
    *,
    light: bool
) -> str:
    """返回终端选中态使用的蓝色语义颜色。"""
    return semantic_color(
        _LIGHT_SELECTION_RGB if light else _DARK_SELECTION_RGB,
        level,
        fallback="ansiblue",
    )


def blend_color(
    color: RgbColor,
    background: RgbColor,
    amount: float
) -> RgbColor:
    """把颜色按表面混合比例叠加到背景。"""
    ratio = max(0.0, min(1.0, amount))
    overlay_red, overlay_green, overlay_blue = color
    base_red, base_green, base_blue = background

    return (
        round(base_red + (overlay_red - base_red) * ratio),
        round(base_green + (overlay_green - base_green) * ratio),
        round(base_blue + (overlay_blue - base_blue) * ratio),
    )


def is_light_color(color: RgbColor) -> bool:
    """按加权亮度判断终端背景是否为浅色。"""
    red, green, blue = color
    return 0.299 * red + 0.587 * green + 0.114 * blue > 128.0


def _hex(color: RgbColor) -> str:
    """将 RGB 元组格式化为大写十六进制颜色。"""
    return "#%02X%02X%02X" % color


def _nearest_xterm_index(color: RgbColor) -> int:
    """在 xterm 256 色表中寻找感知距离最近的颜色。"""
    palette = _xterm_palette()
    return min(
        range(16, len(palette)),
        key=lambda index: _perceptual_distance(color, palette[index]),
    )


def _perceptual_distance(first: RgbColor, second: RgbColor) -> float:
    """按 CIE76 Lab 距离选择最近颜色。"""
    left = _rgb_to_lab(first)
    right = _rgb_to_lab(second)
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _rgb_to_lab(color: RgbColor) -> tuple[float, float, float]:
    """将 sRGB 转换为 D65 XYZ 下的 Lab。"""
    red, green, blue = (
        _srgb_to_linear(component)
        for component in color
    )
    x = red * 0.4124 + green * 0.3576 + blue * 0.1805
    y = red * 0.2126 + green * 0.7152 + blue * 0.0722
    z = red * 0.0193 + green * 0.1192 + blue * 0.9505

    def lab_curve(value: float) -> float:
        return value ** (1.0 / 3.0) if value > 0.008856 else 7.787 * value + 16.0 / 116.0

    fx = lab_curve(x / 0.95047)
    fy = lab_curve(y)
    fz = lab_curve(z / 1.08883)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def _srgb_to_linear(component: int) -> float:
    """将一个八位 sRGB 分量转换为线性 RGB。"""
    value = component / 255.0
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _xterm_palette() -> tuple[RgbColor, ...]:
    """生成标准 xterm 256 色表。"""
    ansi: tuple[RgbColor, ...] = (
        (0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0),
        (0, 0, 238), (205, 0, 205), (0, 205, 205), (229, 229, 229),
        (127, 127, 127), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (92, 92, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    )
    cube = tuple(
        (0 if value == 0 else 55 + value * 40)
        for value in range(6)
    )
    colors: list[RgbColor] = list(ansi)
    colors.extend((red, green, blue) for red in cube for green in cube for blue in cube)
    colors.extend((value, value, value) for value in range(8, 239, 10))
    return tuple(colors)


if __name__ == '__main__':
    pass
