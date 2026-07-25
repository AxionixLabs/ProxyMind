# -*- coding: utf-8 -*-

import pytest

from mind_core.design.terminal_capabilities import (
    TerminalColorLevel,
    TerminalKind,
    TerminalTheme,
    detect_terminal_capabilities,
    detect_terminal_color_level,
    detect_terminal_identity,
    parse_terminal_color_responses,
)


class _InteractiveStream(object):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("environ", "expected"),
    (
        ({"WT_SESSION": "1"}, TerminalKind.WINDOWS_TERMINAL),
        ({"TERM_PROGRAM": "iTerm.app"}, TerminalKind.ITERM2),
        ({"WEZTERM_VERSION": "20240203"}, TerminalKind.WEZTERM),
        ({"TERM_PROGRAM": "ghostty"}, TerminalKind.GHOSTTY),
        ({"KITTY_WINDOW_ID": "1"}, TerminalKind.KITTY),
        ({"ALACRITTY_SOCKET": "socket"}, TerminalKind.ALACRITTY),
        ({"KONSOLE_VERSION": "240800"}, TerminalKind.KONSOLE),
        ({"TERM": "xterm-foot"}, TerminalKind.FOOT),
        ({"TERM_PROGRAM": "Rio"}, TerminalKind.RIO),
        ({"TERM_PROGRAM": "WarpTerminal"}, TerminalKind.WARP),
    ),
)
def test_known_high_capability_terminal_identity(environ, expected) -> None:
    identity = detect_terminal_identity(environ)

    assert identity.kind == expected
    assert identity.high_capability


def test_term_program_prevents_inherited_windows_terminal_false_positive() -> None:
    identity = detect_terminal_identity({
        "TERM_PROGRAM": "vscode",
        "WT_SESSION": "inherited",
    })

    assert identity.kind == TerminalKind.VSCODE
    assert not identity.high_capability


def test_tmux_uses_outer_client_terminal_identity() -> None:
    identity = detect_terminal_identity(
        {"TMUX": "/tmp/tmux"},
        tmux_probe=lambda: ("xterm-kitty", "xterm-256color"),
    )

    assert identity.kind == TerminalKind.KITTY
    assert identity.multiplexer == TerminalKind.TMUX
    assert identity.high_capability


@pytest.mark.parametrize(
    ("environ", "expected"),
    (
        ({"FORCE_COLOR": "0", "WT_SESSION": "1"}, TerminalColorLevel.UNKNOWN),
        ({"FORCE_COLOR": "2"}, TerminalColorLevel.ANSI256),
        ({"FORCE_COLOR": "3"}, TerminalColorLevel.TRUECOLOR),
        ({"NO_COLOR": "1", "COLORTERM": "truecolor"}, TerminalColorLevel.UNKNOWN),
        ({"WT_SESSION": "1"}, TerminalColorLevel.TRUECOLOR),
        ({"COLORTERM": "truecolor"}, TerminalColorLevel.TRUECOLOR),
        ({"TERM": "xterm-256color"}, TerminalColorLevel.ANSI256),
        ({"TERM": "xterm-color"}, TerminalColorLevel.ANSI16),
        ({"TERM": "dumb"}, TerminalColorLevel.UNKNOWN),
    ),
)
def test_terminal_color_level_honors_overrides_and_capabilities(
    environ,
    expected,
) -> None:
    assert detect_terminal_color_level(environ) == expected


def test_capability_detection_requires_theme_probe_for_dynamic_surface() -> None:
    stream = _InteractiveStream()
    calls: list[float] = []

    def probe(_input, _output, timeout) -> TerminalTheme:
        calls.append(timeout)
        return TerminalTheme(
            foreground=(230, 230, 230),
            background=(12, 18, 24),
        )

    capabilities = detect_terminal_capabilities(
        input_stream=stream,
        output_stream=stream,
        environ={"TERM_PROGRAM": "WezTerm"},
        color_probe=probe,
    )

    assert capabilities.dynamic_surfaces
    assert capabilities.theme.background == (12, 18, 24)
    assert calls == [0.1]


def test_unknown_terminal_does_not_run_active_color_probe() -> None:
    stream = _InteractiveStream()
    calls: list[bool] = []

    def probe(_input, _output, _timeout) -> TerminalTheme:
        calls.append(True)
        return TerminalTheme(background=(0, 0, 0))

    capabilities = detect_terminal_capabilities(
        input_stream=stream,
        output_stream=stream,
        environ={"COLORTERM": "truecolor", "TERM": "xterm"},
        color_probe=probe,
    )

    assert not capabilities.dynamic_surfaces
    assert not calls


def test_osc_color_response_parses_eight_and_sixteen_bit_rgb() -> None:
    theme = parse_terminal_color_responses(
        b"\x1b]10;rgb:eeee/dddd/cccc\x1b\\"
        b"\x1b]11;#102030\x07"
    )

    assert theme.foreground == (238, 221, 204)
    assert theme.background == (16, 32, 48)
