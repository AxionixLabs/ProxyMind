# -*- coding: utf-8 -*-

import types
import typing
from types import SimpleNamespace

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.output.win32 import (
    NoConsoleScreenBufferError,
    Win32Output,
)

from frontends.terminal import probe_windows as terminal_probe_windows
from frontends.terminal.capabilities import (
    TerminalCapabilityState,
    TerminalCapabilities,
    TerminalOutputCapabilities,
    detect_terminal_output_capabilities,
    detect_terminal_capabilities,
)
from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSource,
    TerminalColorSupport,
    detect_terminal_color_level,
    detect_terminal_color_support,
)
from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalIdentitySource,
    TerminalKind,
    detect_terminal_identity,
)
from frontends.terminal.probe import (
    TerminalDefaultColors,
    TerminalDefaultColorsCache,
    TerminalProbeMethod,
    filter_terminal_color_responses,
    parse_terminal_color_responses,
)
from frontends.terminal.probe_windows import query_windows_default_colors
from frontends.tui.rendering.screen.terminal import supports_terminal_hyperlinks


class _InteractiveStream(object):
    def isatty(self) -> bool:
        return True


class _OutputStream(_InteractiveStream):
    encoding = "utf-8"

    def write(self, _data: str) -> int:
        return 0

    def flush(self) -> None:
        return None


class _CprVt100Output(Vt100_Output):
    def __init__(
        self,
        *,
        responds_to_cpr: bool,
        get_size: typing.Callable[[], Size],
    ) -> None:
        super().__init__(
            _OutputStream(),
            get_size,
            term="xterm-256color",
        )
        self._responds_to_cpr = responds_to_cpr

    @property
    def responds_to_cpr(self) -> bool:
        return self._responds_to_cpr


class _WindowsConsoleOutput(Win32Output):
    def get_size(self) -> Size:
        return Size(rows=30, columns=120)

    def get_win32_screen_buffer_info(self) -> SimpleNamespace:
        return SimpleNamespace(
            dwCursorPosition=SimpleNamespace(X=12, Y=7),
        )


class _BrokenWindowsConsoleOutput(_WindowsConsoleOutput):
    def get_size(self) -> Size:
        raise OSError("console size unavailable")

    def get_win32_screen_buffer_info(self) -> SimpleNamespace:
        raise NoConsoleScreenBufferError()


class _ConPtyOutput(_WindowsConsoleOutput):
    @property
    def vt100_output(self) -> Vt100_Output:
        return _CprVt100Output(
            responds_to_cpr=False,
            get_size=lambda: Size(rows=24, columns=80),
        )


def test_output_capabilities_are_immutable_named_facts() -> None:
    capabilities = TerminalOutputCapabilities(
        absolute_cursor_addressing=TerminalCapabilityState.SUPPORTED,
    )

    assert capabilities.absolute_cursor_addressing is (
        TerminalCapabilityState.SUPPORTED
    )
    with pytest.raises(AttributeError):
        capabilities.absolute_cursor_addressing = (
            TerminalCapabilityState.UNSUPPORTED
        )


def test_vt_output_capabilities_are_detected_without_terminal_identity() -> None:
    output = _CprVt100Output(
        responds_to_cpr=True,
        get_size=lambda: Size(rows=24, columns=80),
    )

    capabilities = detect_terminal_output_capabilities(output)

    assert capabilities == TerminalOutputCapabilities(
        absolute_cursor_addressing=TerminalCapabilityState.SUPPORTED,
        synchronized_output=TerminalCapabilityState.SUPPORTED,
        viewport_size=TerminalCapabilityState.SUPPORTED,
        startup_cursor_position=TerminalCapabilityState.SUPPORTED,
    )


def test_vt_output_cpr_rejection_is_not_promoted_to_startup_position() -> None:
    output = _CprVt100Output(
        responds_to_cpr=False,
        get_size=lambda: Size(rows=24, columns=80),
    )

    capabilities = detect_terminal_output_capabilities(output)

    assert capabilities.absolute_cursor_addressing is (
        TerminalCapabilityState.SUPPORTED
    )
    assert capabilities.startup_cursor_position is (
        TerminalCapabilityState.UNSUPPORTED
    )


def test_output_probe_failure_is_explicitly_unknown() -> None:
    output = _CprVt100Output(
        responds_to_cpr=True,
        get_size=lambda: (_raise_size_probe_error()),
    )

    capabilities = detect_terminal_output_capabilities(output)

    assert capabilities.absolute_cursor_addressing is (
        TerminalCapabilityState.SUPPORTED
    )
    assert capabilities.viewport_size is TerminalCapabilityState.UNKNOWN
    assert capabilities.startup_cursor_position is (
        TerminalCapabilityState.SUPPORTED
    )


def test_windows_console_output_uses_console_cursor_and_size_adapters() -> None:
    capabilities = detect_terminal_output_capabilities(
        _WindowsConsoleOutput.__new__(_WindowsConsoleOutput),
    )

    assert capabilities == TerminalOutputCapabilities(
        absolute_cursor_addressing=TerminalCapabilityState.SUPPORTED,
        synchronized_output=TerminalCapabilityState.UNSUPPORTED,
        viewport_size=TerminalCapabilityState.SUPPORTED,
        startup_cursor_position=TerminalCapabilityState.SUPPORTED,
    )


def test_windows_console_probe_failure_does_not_claim_position_or_size() -> None:
    capabilities = detect_terminal_output_capabilities(
        _BrokenWindowsConsoleOutput.__new__(_BrokenWindowsConsoleOutput),
    )

    assert capabilities.absolute_cursor_addressing is (
        TerminalCapabilityState.SUPPORTED
    )
    assert capabilities.synchronized_output is (
        TerminalCapabilityState.UNSUPPORTED
    )
    assert capabilities.viewport_size is TerminalCapabilityState.UNKNOWN
    assert capabilities.startup_cursor_position is (
        TerminalCapabilityState.UNKNOWN
    )


def test_conpty_output_combines_vt_writes_with_console_position_probe() -> None:
    capabilities = detect_terminal_output_capabilities(
        _ConPtyOutput.__new__(_ConPtyOutput),
    )

    assert capabilities.absolute_cursor_addressing is (
        TerminalCapabilityState.SUPPORTED
    )
    assert capabilities.synchronized_output is (
        TerminalCapabilityState.SUPPORTED
    )
    assert capabilities.viewport_size is TerminalCapabilityState.SUPPORTED
    assert capabilities.startup_cursor_position is (
        TerminalCapabilityState.SUPPORTED
    )


def test_dummy_and_plain_text_outputs_are_explicitly_unsupported() -> None:
    assert detect_terminal_output_capabilities(DummyOutput()) == (
        TerminalOutputCapabilities(
            absolute_cursor_addressing=TerminalCapabilityState.UNSUPPORTED,
            synchronized_output=TerminalCapabilityState.UNSUPPORTED,
            viewport_size=TerminalCapabilityState.UNSUPPORTED,
            startup_cursor_position=TerminalCapabilityState.UNSUPPORTED,
        )
    )
    assert detect_terminal_output_capabilities(
        PlainTextOutput(_OutputStream()),
    ).viewport_size is TerminalCapabilityState.UNSUPPORTED


def test_missing_output_probe_is_unknown_instead_of_inferred_from_identity() -> None:
    assert detect_terminal_output_capabilities(None) == (
        TerminalOutputCapabilities()
    )


def _raise_size_probe_error() -> Size:
    raise OSError("size probe failed")


def test_terminal_capability_snapshot_keeps_output_facts_separate_from_identity() -> None:
    stream = _InteractiveStream()
    output = _CprVt100Output(
        responds_to_cpr=True,
        get_size=lambda: Size(rows=24, columns=80),
    )

    def probe(_input, _output, _timeout) -> TerminalDefaultColors:
        return TerminalDefaultColors(
            foreground=(240, 240, 240),
            background=(10, 20, 30),
            attempted=True,
            method=TerminalProbeMethod.CUSTOM,
        )

    capabilities = detect_terminal_capabilities(
        input_stream=stream,
        output_stream=stream,
        output_obj=output,
        environ={
            "TERMINAL_EMULATOR": "JetBrains-JediTerm",
            "TERM": "xterm-256color",
        },
        color_probe=probe,
    )

    assert capabilities.identity.kind is TerminalKind.JETBRAINS_JEDITERM
    assert capabilities.output_capabilities.absolute_cursor_addressing is (
        TerminalCapabilityState.SUPPORTED
    )
    assert capabilities.output_capabilities.synchronized_output is (
        TerminalCapabilityState.SUPPORTED
    )


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
def test_known_terminal_identity(environ, expected) -> None:
    identity = detect_terminal_identity(environ)

    assert identity.kind == expected
    assert identity.source is not TerminalIdentitySource.UNKNOWN


def test_term_program_prevents_inherited_windows_terminal_false_positive() -> None:
    identity = detect_terminal_identity({
        "TERM_PROGRAM": "vscode",
        "WT_SESSION": "inherited",
    })

    assert identity.kind == TerminalKind.VSCODE
    assert identity.source_variable == "TERM_PROGRAM"


@pytest.mark.parametrize(
    ("environ", "expected"),
    (
        ({"TERM": "xterm-foot"}, TerminalKind.FOOT),
        ({"TERM_PROGRAM": "Rio"}, TerminalKind.RIO),
        ({"TERM_PROGRAM": "WarpTerminal"}, TerminalKind.WARP),
    ),
)
def test_declared_terminal_aliases_are_reachable(environ, expected) -> None:
    assert detect_terminal_identity(environ).kind is expected


def test_every_declared_terminal_kind_is_reachable() -> None:
    identities = (
        detect_terminal_identity({"WT_SESSION": "session"}),
        detect_terminal_identity({"TERMINAL_EMULATOR": "JetBrains-JediTerm"}),
        detect_terminal_identity({"TERM_PROGRAM": "iTerm.app"}),
        detect_terminal_identity({"TERM_PROGRAM": "WezTerm"}),
        detect_terminal_identity({"TERM_PROGRAM": "ghostty"}),
        detect_terminal_identity({"TERM": "xterm-kitty"}),
        detect_terminal_identity({"TERM": "alacritty"}),
        detect_terminal_identity({"KONSOLE_VERSION": "240800"}),
        detect_terminal_identity({"TERM": "xterm-foot"}),
        detect_terminal_identity({"TERM_PROGRAM": "Rio"}),
        detect_terminal_identity({"TERM_PROGRAM": "WarpTerminal"}),
        detect_terminal_identity({"TERM_PROGRAM": "Apple_Terminal"}),
        detect_terminal_identity({"GNOME_TERMINAL_SCREEN": "screen"}),
        detect_terminal_identity({"TERM_PROGRAM": "vscode"}),
        detect_terminal_identity({"VTE_VERSION": "7600"}),
        detect_terminal_identity(
            {"TMUX": "/tmp/tmux"},
            tmux_probe=lambda: None,
        ),
        detect_terminal_identity({"ZELLIJ": "session"}),
        detect_terminal_identity({"TERM": "dumb"}),
        detect_terminal_identity({}),
    )
    reachable = {identity.kind for identity in identities}
    reachable.update(
        identity.multiplexer
        for identity in identities
        if identity.multiplexer is not None
    )

    assert reachable == set(TerminalKind)


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
    identity = TerminalIdentity(kind, kind.value)

    assert supports_terminal_hyperlinks(identity) is supported


def test_tmux_uses_outer_client_terminal_identity() -> None:
    identity = detect_terminal_identity(
        {"TMUX": "/tmp/tmux"},
        tmux_probe=lambda: ("xterm-kitty", "xterm-256color"),
    )

    assert identity.kind == TerminalKind.KITTY
    assert identity.multiplexer == TerminalKind.TMUX
    assert identity.source is TerminalIdentitySource.TMUX_CLIENT


@pytest.mark.parametrize(
    ("environ", "expected"),
    (
        ({"FORCE_COLOR": "0", "WT_SESSION": "1"}, TerminalColorLevel.NONE),
        ({"FORCE_COLOR": "2"}, TerminalColorLevel.ANSI256),
        ({"FORCE_COLOR": "3"}, TerminalColorLevel.TRUECOLOR),
        ({"NO_COLOR": "1", "COLORTERM": "truecolor"}, TerminalColorLevel.NONE),
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


def test_jediterm_identity_prevents_inherited_windows_terminal_promotion() -> None:
    environ = {
        "TERMINAL_EMULATOR": "JetBrains-JediTerm",
        "WT_SESSION": "inherited",
        "TERM": "xterm-color",
    }
    identity = detect_terminal_identity(environ)
    support = detect_terminal_color_support(environ, identity=identity)

    assert identity.kind is TerminalKind.JETBRAINS_JEDITERM
    assert identity.source_variable == "TERMINAL_EMULATOR"
    assert support.raw_level is TerminalColorLevel.ANSI16
    assert support.effective_level is TerminalColorLevel.ANSI16
    assert support.effective_source is TerminalColorSource.TERM


def test_windows_terminal_promotion_records_raw_and_effective_sources() -> None:
    environ = {"WT_SESSION": "session", "TERM": "xterm-256color"}
    identity = detect_terminal_identity(environ)
    support = detect_terminal_color_support(environ, identity=identity)

    assert support.raw_level is TerminalColorLevel.ANSI256
    assert support.effective_level is TerminalColorLevel.TRUECOLOR
    assert support.raw_source is TerminalColorSource.TERM
    assert support.effective_source is TerminalColorSource.WINDOWS_TERMINAL


def test_no_color_is_recorded_as_an_explicit_disable() -> None:
    support = detect_terminal_color_support({
        "NO_COLOR": "1",
        "COLORTERM": "truecolor",
    })

    assert support.effective_level is TerminalColorLevel.NONE
    assert support.explicitly_disabled
    assert support.effective_source is TerminalColorSource.NO_COLOR


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

    def probe(_input, _output, timeout) -> TerminalDefaultColors:
        calls.append(timeout)
        return TerminalDefaultColors(
            foreground=(230, 230, 230),
            background=(12, 18, 24),
            attempted=True,
            method=TerminalProbeMethod.CUSTOM,
        )

    capabilities = detect_terminal_capabilities(
        input_stream=stream,
        output_stream=stream,
        environ={"TERM_PROGRAM": program, "TERM": "xterm-truecolor"},
        color_probe=probe,
    )

    assert bool(capabilities.theme.background) is supported
    assert capabilities.theme.background == (12, 18, 24)
    assert capabilities.theme.probe_attempted
    assert capabilities.theme.probe_method is TerminalProbeMethod.CUSTOM
    assert calls == [0.1]


def test_unknown_terminal_can_use_active_color_probe_on_a_tty() -> None:
    stream = _InteractiveStream()
    calls: list[bool] = []

    def probe(_input, _output, _timeout) -> TerminalDefaultColors:
        calls.append(True)
        return TerminalDefaultColors(
            foreground=(255, 255, 255),
            background=(0, 0, 0),
            attempted=True,
            method=TerminalProbeMethod.CUSTOM,
        )

    capabilities = detect_terminal_capabilities(
        input_stream=stream,
        output_stream=stream,
        environ={"COLORTERM": "truecolor", "TERM": "xterm"},
        color_probe=probe,
    )

    assert capabilities.theme.background == (0, 0, 0)
    assert calls == [True]


def test_terminal_default_colors_cache_probes_only_once() -> None:
    cache = TerminalDefaultColorsCache()
    calls: list[int] = []

    def probe(_input, _output, _timeout) -> TerminalDefaultColors:
        calls.append(1)
        return TerminalDefaultColors(
            foreground=(240, 240, 240),
            background=(1, 2, 3),
            attempted=True,
            method=TerminalProbeMethod.CUSTOM,
        )

    first = cache.get_or_probe(object(), object(), 0.1, probe)
    second = cache.get_or_probe(object(), object(), 0.1, probe)

    assert first == second == TerminalDefaultColors(
        foreground=(240, 240, 240),
        background=(1, 2, 3),
        attempted=True,
        method=TerminalProbeMethod.CUSTOM,
    )
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
    ) is TerminalColorLevel.NONE


def test_unix_probe_replay_separates_osc_responses_from_input() -> None:
    replayed = filter_terminal_color_responses(
        b"a\x1b]10;#010203\x07b\x1b]11;#040506\x07c",
    )

    assert replayed == b"abc"


def test_windows_probe_does_not_read_input_stream(monkeypatch) -> None:
    expected = TerminalDefaultColors(
        foreground=(1, 2, 3),
        background=(4, 5, 6),
        attempted=True,
        method=TerminalProbeMethod.WINDOWS_CONSOLE,
    )
    monkeypatch.setattr(
        terminal_probe_windows,
        "_windows_palette_colors",
        lambda _output: expected,
    )

    class InputStream(object):
        def fileno(self):
            raise AssertionError("Windows probe must not inspect stdin")

    assert query_windows_default_colors(
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
