# -*- coding: utf-8 -*-

import types

import pytest

from mind_app.presentation.terminal import capabilities as terminal_capabilities
from mind_app.presentation.terminal.capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
    TerminalTheme,
    TerminalThemeCache,
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
        ({"TERM_PROGRAM": "Apple_Terminal"}, TerminalKind.APPLE_TERMINAL),
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


@pytest.mark.parametrize(
    "environ",
    (
        {"TERM": "xterm-foot"},
        {"TERM_PROGRAM": "Rio"},
        {"TERM_PROGRAM": "WarpTerminal"},
    ),
)
def test_non_codex_terminal_aliases_remain_unknown(environ) -> None:
    assert detect_terminal_identity(environ).kind is TerminalKind.UNKNOWN


@pytest.mark.parametrize(
    ("kind", "supported"),
    (
        (TerminalKind.ITERM2, True),
        (TerminalKind.VSCODE, True),
        (TerminalKind.VTE, True),
        (TerminalKind.APPLE_TERMINAL, False),
        (TerminalKind.UNKNOWN, False),
        (TerminalKind.TMUX, False),
    ),
)
def test_terminal_hyperlinks_use_known_osc8_capabilities(
    kind: TerminalKind,
    supported: bool,
) -> None:
    capabilities = TerminalCapabilities(
        TerminalIdentity(kind, kind.value),
        TerminalColorLevel.UNKNOWN,
    )

    assert capabilities.hyperlinks is supported


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
        (
            {"TERM_PROGRAM": "WindowsTerminal", "TERM": "xterm-color"},
            TerminalColorLevel.TRUECOLOR,
        ),
        ({"TERM_PROGRAM": "Apple_Terminal"}, TerminalColorLevel.UNKNOWN),
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


@pytest.mark.parametrize(
    ("program", "supported"),
    (("WezTerm", True), ("Apple_Terminal", True)),
)
def test_dynamic_surface_probe_depends_on_terminal_support(
    program: str,
    supported: bool,
) -> None:
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
        environ={"TERM_PROGRAM": program, "TERM": "xterm-truecolor"},
        color_probe=probe,
    )

    assert capabilities.dynamic_surfaces is supported
    assert capabilities.theme.background == (12, 18, 24)
    assert calls == [0.1]


def test_unknown_terminal_can_use_active_color_probe_on_a_tty() -> None:
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

    assert capabilities.dynamic_surfaces
    assert calls == [True]


def test_terminal_theme_cache_probes_only_once() -> None:
    cache = TerminalThemeCache()
    calls: list[int] = []

    def probe(_input, _output, _timeout) -> TerminalTheme:
        calls.append(1)
        return TerminalTheme(background=(1, 2, 3))

    first = cache.get_or_probe(object(), object(), 0.1, probe)
    second = cache.get_or_probe(object(), object(), 0.1, probe)

    assert first == second == TerminalTheme(background=(1, 2, 3))
    assert calls == [1]


def test_multiplexer_detection_precedes_direct_program_signal() -> None:
    identity = detect_terminal_identity({
        "ZELLIJ": "0",
        "ZELLIJ_SESSION_NAME": "session",
        "TERM_PROGRAM": "vscode",
        "TERM": "xterm-256color",
    })

    assert identity.kind is TerminalKind.VSCODE
    assert identity.multiplexer is TerminalKind.ZELLIJ


def test_terminal_identity_retains_program_version_and_tmux_term() -> None:
    identity = detect_terminal_identity({
        "TERM_PROGRAM": "iTerm.app",
        "TERM_PROGRAM_VERSION": "3.6",
        "TERM": "xterm-256color",
    })

    assert identity.term_program == "iTerm.app"
    assert identity.version == "3.6"
    assert identity.term == "xterm-256color"


def test_zellij_version_signal_is_enough_to_detect_multiplexer() -> None:
    identity = detect_terminal_identity({
        "ZELLIJ_VERSION": "0.40.1",
        "TERM": "xterm-256color",
    })

    assert identity.multiplexer is TerminalKind.ZELLIJ
    assert identity.multiplexer_version == "0.40.1"


def test_non_tty_output_does_not_claim_color_without_force() -> None:
    stream = types.SimpleNamespace(isatty=lambda: False)

    assert detect_terminal_color_level(
        {"TERM": "xterm-256color"},
        output_stream=stream,
    ) is TerminalColorLevel.UNKNOWN


def test_unix_probe_replay_separates_osc_responses_from_input() -> None:
    replayed: list[bytes] = []

    terminal_capabilities._replay_non_color_input(
        b"a\x1b]10;#010203\x07b\x1b]11;#040506\x07c",
        replayed.append,
    )

    assert replayed == [b"a", b"b", b"c"]


def test_windows_probe_does_not_read_input_stream(monkeypatch) -> None:
    expected = TerminalTheme(foreground=(1, 2, 3), background=(4, 5, 6))
    monkeypatch.setattr(
        terminal_capabilities,
        "_windows_palette_theme",
        lambda _output: expected,
    )

    class InputStream(object):
        def fileno(self):
            raise AssertionError("Windows probe must not inspect stdin")

    assert terminal_capabilities._query_windows_theme(
        InputStream(),
        object(),
        0.1,
    ) == expected


def test_osc_color_response_parses_eight_and_sixteen_bit_rgb() -> None:
    theme = parse_terminal_color_responses(
        b"\x1b]10;rgb:eeee/dddd/cccc\x1b\\"
        b"\x1b]11;#102030\x07"
    )

    assert theme.foreground == (238, 221, 204)
    assert theme.background == (16, 32, 48)


def test_osc_rgba_response_ignores_alpha_after_validation() -> None:
    theme = parse_terminal_color_responses(
        b"\x1b]11;rgba:1122/3344/5566/ffff\x1b\\"
    )

    assert theme.background == (17, 51, 85)
