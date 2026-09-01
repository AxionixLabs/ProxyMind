from frontends.terminal.capabilities import TerminalColorLevel
from frontends.terminal.palette import best_color, blend_color, semantic_color


def test_best_color_matches_codex_diff_xterm_targets() -> None:
    assert best_color((33, 58, 43), TerminalColorLevel.TRUECOLOR) == "#213A2B"
    assert best_color((0, 95, 0), TerminalColorLevel.ANSI256) == "#005F00"
    assert best_color((175, 255, 175), TerminalColorLevel.ANSI256) == "#AFFFAF"


def test_ansi16_uses_named_fallback() -> None:
    assert best_color((0, 95, 135), TerminalColorLevel.ANSI16) is None
    assert semantic_color(
        (0, 95, 135),
        TerminalColorLevel.ANSI16,
        fallback="ansicyan",
    ) == "ansicyan"


def test_blend_color_clamps_ratio() -> None:
    assert blend_color((255, 255, 255), (0, 0, 0), 0.12) == (31, 31, 31)
    assert blend_color((255, 255, 255), (0, 0, 0), 2) == (255, 255, 255)
