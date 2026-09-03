# -*- coding: utf-8 -*-

import pytest

from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from frontends.terminal.semantic_styles import (
    TerminalThemeTone,
    resolve_terminal_semantic_styles,
)


@pytest.mark.parametrize(
    ("level", "expected"),
    (
        (TerminalColorLevel.TRUECOLOR, "#005F87"),
        (TerminalColorLevel.ANSI256, "#005F87"),
        (TerminalColorLevel.ANSI16, "ansicyan"),
    ),
)
def test_light_theme_uses_deep_cyan_accent(
    level: TerminalColorLevel,
    expected: str,
) -> None:
    styles = resolve_terminal_semantic_styles(
        TerminalColorSupport.fixed(level),
        foreground=(0, 0, 0),
        background=(255, 255, 255),
    )

    assert styles.tone is TerminalThemeTone.LIGHT
    assert styles.accent.foreground == expected
    assert styles.selected.foreground == expected


@pytest.mark.parametrize(
    "level",
    (TerminalColorLevel.TRUECOLOR, TerminalColorLevel.ANSI256, TerminalColorLevel.ANSI16),
)
def test_dark_theme_uses_terminal_ansi_semantics(level: TerminalColorLevel) -> None:
    styles = resolve_terminal_semantic_styles(
        TerminalColorSupport.fixed(level),
        foreground=(255, 255, 255),
        background=(0, 0, 0),
    )

    assert styles.tone is TerminalThemeTone.DARK
    assert styles.accent.foreground == "ansicyan"
    assert styles.success.foreground == "ansigreen"
    assert styles.failure.foreground == "ansired"
    assert styles.attention.foreground == "ansiyellow"
    assert styles.brand.foreground == "ansimagenta"


def test_disabled_color_never_uses_fallback_color() -> None:
    styles = resolve_terminal_semantic_styles(
        TerminalColorSupport.fixed(TerminalColorLevel.NONE),
        foreground=(255, 255, 255),
        background=(0, 0, 0),
    )

    assert styles.accent.foreground is None
    assert styles.success.foreground is None
    assert styles.failure.foreground is None
    assert styles.attention.foreground is None
    assert styles.brand.foreground is None
    assert styles.user_surface.background is None
    assert styles.selected_surface.background is None
    assert styles.selected_surface.reverse


def test_unknown_color_level_uses_only_basic_ansi_semantics() -> None:
    styles = resolve_terminal_semantic_styles(
        TerminalColorSupport.fixed(TerminalColorLevel.UNKNOWN),
        foreground=(255, 255, 255),
        background=(0, 0, 0),
    )

    assert styles.accent.foreground == "ansicyan"
    assert styles.success.foreground == "ansigreen"
    assert styles.failure.foreground == "ansired"
    assert styles.attention.foreground == "ansiyellow"
    assert styles.user_surface.background is None
    assert styles.selected_surface.background is None


def test_rich_dark_theme_resolves_surfaces_and_separator_once() -> None:
    styles = resolve_terminal_semantic_styles(
        TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
        foreground=(255, 255, 255),
        background=(0, 0, 0),
    )

    assert styles.user_surface.background == "#1F1F1F"
    assert styles.approval_surface.background == "#1F1F1F"
    assert styles.selected_surface.background == "#1F1F1F"
    assert styles.zebra_surface.background == "#0E0E0E"
    assert styles.separator.foreground == "#333333"


def test_ansi16_never_creates_surface_backgrounds() -> None:
    styles = resolve_terminal_semantic_styles(
        TerminalColorSupport.fixed(TerminalColorLevel.ANSI16),
        foreground=(255, 255, 255),
        background=(0, 0, 0),
    )

    assert styles.user_surface.background is None
    assert styles.approval_surface.background is None
    assert styles.zebra_surface.background is None


if __name__ == '__main__':
    pass
