# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass
from enum import Enum

from agent.ports.presentation import TextStyle
from .color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from .palette import (
    best_color,
    blend_color,
    is_light_color,
)
from .probe import RgbColor

_LIGHT_ACCENT_RGB: RgbColor = (0, 95, 135)
_DARK_BRAND_RGB: RgbColor = (45, 212, 191)
_LIGHT_BRAND_RGB: RgbColor = (0, 105, 92)


class TerminalSemanticRole(str, Enum):
    """描述普通终端文本可使用的稳定语义角色。"""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    ACCENT = "accent"
    SELECTED = "selected"
    SUCCESS = "success"
    FAILURE = "failure"
    ATTENTION = "attention"
    BRAND = "brand"


_ANSI_ROLE_COLORS: dict[TerminalSemanticRole, str | None] = {
    TerminalSemanticRole.PRIMARY: None,
    TerminalSemanticRole.SECONDARY: None,
    TerminalSemanticRole.ACCENT: "ansicyan",
    TerminalSemanticRole.SELECTED: "ansicyan",
    TerminalSemanticRole.SUCCESS: "ansigreen",
    TerminalSemanticRole.FAILURE: "ansired",
    TerminalSemanticRole.ATTENTION: "ansiyellow",
    TerminalSemanticRole.BRAND: "ansibrightcyan",
}

_RICH_ROLE_COLORS: dict[TerminalSemanticRole, str | None] = {
    TerminalSemanticRole.PRIMARY: None,
    TerminalSemanticRole.SECONDARY: None,
    TerminalSemanticRole.ACCENT: "cyan",
    TerminalSemanticRole.SELECTED: "cyan",
    TerminalSemanticRole.SUCCESS: "green",
    TerminalSemanticRole.FAILURE: "red",
    TerminalSemanticRole.ATTENTION: "yellow",
    TerminalSemanticRole.BRAND: "bright_cyan",
}


def semantic_text_style(
    role: TerminalSemanticRole,
    *,
    bold: bool | None = None,
    dim: bool | None = None,
    italic: bool = False,
    underline: bool = False,
) -> TextStyle:
    """把语义角色转换为跨终端前端共享的 ANSI 文本样式。"""

    default_bold = role is TerminalSemanticRole.SELECTED
    default_dim = role is TerminalSemanticRole.SECONDARY
    return TextStyle(
        foreground=_ANSI_ROLE_COLORS[role],
        bold=default_bold if bold is None else bold,
        dim=default_dim if dim is None else dim,
        italic=italic,
        underline=underline,
    )


def semantic_role_for_ansi_color(value: str | None) -> TerminalSemanticRole | None:
    """返回 ANSI 命名色在普通终端文本中的语义角色。"""

    color = str(value or "").strip().casefold()
    return {
        "ansicyan": TerminalSemanticRole.ACCENT,
        "ansigreen": TerminalSemanticRole.SUCCESS,
        "ansired": TerminalSemanticRole.FAILURE,
        "ansiyellow": TerminalSemanticRole.ATTENTION,
        "ansibrightcyan": TerminalSemanticRole.BRAND,
    }.get(color)


def semantic_rich_style(
    role: TerminalSemanticRole,
    *,
    bold: bool = False,
    dim: bool = False,
) -> str:
    """把语义角色转换为 Rich 使用的命名色样式。"""

    parts = [
        name
        for enabled, name in ((bold, "bold"), (dim, "dim"))
        if enabled
    ]
    if color := _RICH_ROLE_COLORS[role]:
        parts.append(color)
    return " ".join(parts)


class TerminalThemeTone(str, Enum):
    """描述默认终端背景的明暗结论。"""

    DARK = "dark"
    LIGHT = "light"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TerminalStyle:
    """描述与具体 TUI 组件无关的终端语义样式。"""

    foreground: str | None = None
    background: str | None = None
    bold: bool = False
    dim: bool = False
    reverse: bool = False


@dataclass(frozen=True)
class TerminalSemanticStyles:
    """保存一次终端会话解析后的全部通用语义 token。"""

    tone: TerminalThemeTone
    primary: TerminalStyle
    secondary: TerminalStyle
    accent: TerminalStyle
    accent_plain: TerminalStyle
    selected: TerminalStyle
    success: TerminalStyle
    failure: TerminalStyle
    attention: TerminalStyle
    brand: TerminalStyle
    separator: TerminalStyle
    user_surface: TerminalStyle
    approval_surface: TerminalStyle
    selected_surface: TerminalStyle
    zebra_surface: TerminalStyle


def resolve_terminal_semantic_styles(
    color_support: TerminalColorSupport,
    *,
    foreground: RgbColor | None,
    background: RgbColor | None,
) -> TerminalSemanticStyles:
    """按色深和默认颜色一次性解析全部终端语义 token。"""

    level = color_support.effective_level
    colors_enabled = level is not TerminalColorLevel.NONE
    tone = _theme_tone(background)

    accent_color: str | None = None
    success_color: str | None = None
    failure_color: str | None = None
    attention_color: str | None = None
    brand_color: str | None = None
    if colors_enabled:
        accent_color = (
            best_color(_LIGHT_ACCENT_RGB, level) or "ansicyan"
            if tone is TerminalThemeTone.LIGHT
            else "ansicyan"
        )
        success_color = "ansigreen"
        failure_color = "ansired"
        brand_color = "ansibrightcyan"
        if (
            level in {
                TerminalColorLevel.TRUECOLOR,
                TerminalColorLevel.ANSI256,
            }
            and tone is not TerminalThemeTone.UNKNOWN
        ):
            brand_rgb = (
                _LIGHT_BRAND_RGB
                if tone is TerminalThemeTone.LIGHT
                else _DARK_BRAND_RGB
            )
            brand_color = best_color(brand_rgb, level) or "ansibrightcyan"
        if tone is TerminalThemeTone.DARK:
            attention_color = "ansiyellow"

    separator_color: str | None = None
    surface_color: str | None = None
    selected_surface_color: str | None = None
    zebra_surface_color: str | None = None
    if (
        level in {TerminalColorLevel.TRUECOLOR, TerminalColorLevel.ANSI256}
        and background is not None
    ):
        overlay = (0, 0, 0) if tone is TerminalThemeTone.LIGHT else (255, 255, 255)
        surface_ratio = 0.04 if tone is TerminalThemeTone.LIGHT else 0.12
        surface_color = best_color(blend_color(overlay, background, surface_ratio), level)
        selected_surface_color = best_color(
            blend_color(overlay, background, 0.12),
            level,
        )
        zebra_surface_color = best_color(
            blend_color(
                overlay,
                background,
                0.04 if tone is TerminalThemeTone.LIGHT else 0.055,
            ),
            level,
        )
        if foreground is not None:
            separator_color = best_color(
                blend_color(foreground, background, 0.20),
                level,
            )

    return TerminalSemanticStyles(
        tone=tone,
        primary=TerminalStyle(),
        secondary=TerminalStyle(dim=True),
        accent=TerminalStyle(foreground=accent_color, bold=True),
        accent_plain=TerminalStyle(foreground=accent_color),
        selected=TerminalStyle(foreground=accent_color, bold=True),
        success=TerminalStyle(foreground=success_color),
        failure=TerminalStyle(foreground=failure_color),
        attention=TerminalStyle(foreground=attention_color, bold=True),
        brand=TerminalStyle(foreground=brand_color),
        separator=TerminalStyle(
            foreground=separator_color,
            dim=separator_color is None,
        ),
        user_surface=TerminalStyle(background=surface_color),
        approval_surface=TerminalStyle(background=surface_color),
        selected_surface=TerminalStyle(
            background=selected_surface_color,
            reverse=selected_surface_color is None,
        ),
        zebra_surface=TerminalStyle(background=zebra_surface_color),
    )


def _theme_tone(background: RgbColor | None) -> TerminalThemeTone:
    """把可选默认背景解析为明暗主题。"""

    if background is None:
        return TerminalThemeTone.UNKNOWN
    if is_light_color(background):
        return TerminalThemeTone.LIGHT
    return TerminalThemeTone.DARK


if __name__ == '__main__':
    pass
