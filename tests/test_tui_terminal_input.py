import contextlib
import typing

import pytest
import frontends.tui.adapters.keyboard as keyboard_adapter
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys

from frontends.terminal.identity import TerminalIdentity
from frontends.terminal.identity import TerminalKind
from frontends.tui.adapters.keyboard import EnhancedVt100Parser
from frontends.tui.adapters.keyboard import TerminalKeyboardInputAdapter
from frontends.tui.adapters.keyboard import TerminalKeyboardMode
from frontends.tui.adapters.input import FocusEventInputAdapter
from frontends.tui.adapters.input import WindowsUnicodeInputAdapter
from frontends.tui.contracts.keyboard import enhanced_key_token
from frontends.tui.core.keymap import TuiRuntimeKeymap
from frontends.tui.core.keymap import key_action_matches


class _BatchInput(Input):
    """按调用顺序返回固定按键批次。"""

    def __init__(self, batches: tuple[tuple[KeyPress, ...], ...]) -> None:
        self._batches = list(batches)
        self.closed_value = False

    @property
    def closed(self) -> bool:
        """返回测试输入是否关闭。"""
        return self.closed_value

    def fileno(self) -> int:
        """返回稳定测试描述符。"""
        return 7

    def typeahead_hash(self) -> str:
        """返回稳定测试身份。"""
        return "batch-input"

    def read_keys(self) -> list[KeyPress]:
        """读取下一批按键。"""
        if not self._batches:
            return []
        return list(self._batches.pop(0))

    def raw_mode(self) -> typing.ContextManager[None]:
        """返回空 raw mode 上下文。"""
        return contextlib.nullcontext()

    def cooked_mode(self) -> typing.ContextManager[None]:
        """返回空 cooked mode 上下文。"""
        return contextlib.nullcontext()

    def attach(
        self,
        _input_ready_callback: typing.Callable[[], None],
    ) -> typing.ContextManager[None]:
        """返回空事件循环挂接上下文。"""
        return contextlib.nullcontext()

    def detach(self) -> typing.ContextManager[None]:
        """返回空事件循环解除挂接上下文。"""
        return contextlib.nullcontext()


class _RecordingOutput:
    """记录终端模式写入且模拟 Windows VT 输出能力。"""

    vt100_output = True

    def __init__(self) -> None:
        self.raw = ""
        self.flush_count = 0

    def write_raw(self, text: str) -> None:
        """记录未转义的控制序列。"""
        self.raw += text

    def flush(self) -> None:
        """记录一次立即下发。"""
        self.flush_count += 1


def test_focus_event_input_consumes_events_and_preserves_adjacent_keys() -> None:
    """验证 focus 事件不会进入正文且相邻用户输入保持顺序。"""
    source = _BatchInput(((
        KeyPress(Keys.Escape, "\x1b"),
        KeyPress("[", "["),
        KeyPress("I", "I"),
        KeyPress("a", "a"),
        KeyPress(Keys.Escape, "\x1b"),
        KeyPress("[", "["),
        KeyPress("O", "O"),
        KeyPress("b", "b"),
    ),))
    input_obj = FocusEventInputAdapter(source)

    assert input_obj.read_keys() == [
        KeyPress("a", "a"),
        KeyPress("b", "b"),
    ]


def test_focus_event_input_handles_split_sequences_and_flushes_text() -> None:
    """验证跨批 focus 序列只消费一次且普通 Escape 前缀可冲刷。"""
    source = _BatchInput((
        (
            KeyPress(Keys.Escape, "\x1b"),
            KeyPress("[", "["),
        ),
        (
            KeyPress("I", "I"),
            KeyPress("x", "x"),
            KeyPress(Keys.Escape, "\x1b"),
            KeyPress("[", "["),
        ),
    ))
    input_obj = FocusEventInputAdapter(source)

    assert input_obj.read_keys() == []
    assert input_obj.read_keys() == [KeyPress("x", "x")]
    assert input_obj.flush_keys() == [
        KeyPress(Keys.Escape, "\x1b"),
        KeyPress("[", "["),
    ]


def test_windows_unicode_input_merges_surrogates_across_reads() -> None:
    """验证非 BMP 字符跨底层读取批次仍形成一个按键。"""
    source = _BatchInput((
        (KeyPress("\ud83d", "\ud83d"),),
        (
            KeyPress("\ude42", "\ude42"),
            KeyPress("\u0301", "\u0301"),
        ),
    ))
    input_obj = WindowsUnicodeInputAdapter(source)

    assert input_obj.read_keys() == []
    assert input_obj.read_keys() == [
        KeyPress("🙂", "🙂"),
        KeyPress("\u0301", "\u0301"),
    ]


def test_windows_unicode_input_replaces_unpaired_surrogates_on_flush() -> None:
    """验证 flush 不向编辑器泄漏孤立 UTF-16 码元。"""
    source = _BatchInput(((KeyPress("\ud83d", "\ud83d"),),))
    input_obj = WindowsUnicodeInputAdapter(source)

    assert input_obj.read_keys() == []
    assert input_obj.flush_keys() == [KeyPress("\ufffd", "\ufffd")]


def test_windows_unicode_input_normalizes_bracketed_paste_payload() -> None:
    """验证粘贴载荷中的合法 surrogate 对和孤立码元同时收敛。"""
    source = _BatchInput(((KeyPress(
        Keys.BracketedPaste,
        "paste-\ud83d\ude42-\ud83d",
    ),),))
    input_obj = WindowsUnicodeInputAdapter(source)

    assert input_obj.read_keys() == [KeyPress(
        Keys.BracketedPaste,
        "paste-🙂-\ufffd",
    )]


def test_enhanced_parser_preserves_split_press_and_repeat_events() -> None:
    keys: list[KeyPress] = []
    parser = EnhancedVt100Parser(keys.append)

    parser.feed("\x1b")
    parser.feed("[109;5")
    parser.feed("u\x1b[13;2:2u")

    assert keys == [
        KeyPress(enhanced_key_token("m", frozenset({"ctrl"})), ""),
        KeyPress(enhanced_key_token("enter", frozenset({"shift"})), ""),
    ]


def test_enhanced_parser_ignores_release_query_and_unsupported_modifiers() -> None:
    keys: list[KeyPress] = []
    parser = EnhancedVt100Parser(keys.append)

    parser.feed("\x1b[?7u")
    parser.feed("\x1b[13;2:3u")
    parser.feed("\x1b[111;9u")

    assert keys == []


def test_enhanced_parser_preserves_plain_escape_after_flush() -> None:
    keys: list[KeyPress] = []
    parser = EnhancedVt100Parser(keys.append)

    parser.feed("\x1b")
    assert keys == []
    parser.flush()

    assert keys == [KeyPress(Keys.Escape, "\x1b")]


def test_enhanced_parser_decodes_modify_other_keys_and_named_events() -> None:
    keys: list[KeyPress] = []
    parser = EnhancedVt100Parser(keys.append)

    parser.feed("\x1b[27;3;111~\x1b[1;6:2A")

    assert keys == [
        KeyPress(enhanced_key_token("o", frozenset({"alt"})), ""),
        KeyPress(
            enhanced_key_token("up", frozenset({"ctrl", "shift"})),
            "",
        ),
    ]


def test_enhanced_parser_keeps_csi_u_text_inside_bracketed_paste() -> None:
    keys: list[KeyPress] = []
    parser = EnhancedVt100Parser(keys.append)

    parser.feed("\x1b[200~before\x1b[13;2uafter\x1b[201~")

    assert keys == [KeyPress(
        Keys.BracketedPaste,
        "before\x1b[13;2uafter",
    )]


@pytest.mark.parametrize(
    ("sequence", "action_id"),
    (
        ("\x1b[109;5u", "editor.insert_newline"),
        ("\x1b[13;2u", "editor.insert_newline"),
        ("\x1b[127;2u", "editor.delete_backward"),
        ("\x1b[104;5u", "editor.delete_backward"),
        ("\x1b[127;5u", "editor.delete_word_backward"),
        ("\x1b[127;6u", "editor.delete_word_backward"),
        ("\x1b[104;7u", "editor.delete_word_backward"),
        ("\x1b[3;2:1~", "editor.delete_forward"),
        ("\x1b[3;3:1~", "editor.delete_word_forward"),
        ("\x1b[3;5:1~", "editor.delete_word_forward"),
        ("\x1b[3;6:1~", "editor.delete_word_forward"),
        ("\x1b[3;6~", "editor.delete_word_forward"),
        ("\x1b[104;5u", "list.move_left"),
        ("\x1b[97;6u", "approval.expand_details"),
        ("\x1b[32;2u", "pager.page_up"),
    ),
)
def test_enhanced_sequences_reach_codex_aligned_runtime_actions(
    sequence: str,
    action_id: str,
) -> None:
    keys: list[KeyPress] = []
    parser = EnhancedVt100Parser(keys.append)
    parser.feed(sequence)

    keymap = TuiRuntimeKeymap.defaults()
    assert len(keys) == 1
    assert key_action_matches(
        keymap.bindings_for(action_id),
        tuple(key.key for key in keys),
    )


def test_terminal_keyboard_mode_matches_codex_flags_and_restores() -> None:
    wezterm_output = _RecordingOutput()
    wezterm_mode = TerminalKeyboardMode(
        wezterm_output,
        TerminalIdentity(TerminalKind.WEZTERM, "WezTerm"),
        {},
    )
    wezterm_mode.enable()
    wezterm_mode.restore()

    iterm_output = _RecordingOutput()
    iterm_mode = TerminalKeyboardMode(
        iterm_output,
        TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
        {},
    )
    iterm_mode.enable()
    iterm_mode.restore()

    assert wezterm_output.raw == (
        "\x1b[>4;0m\x1b[>7u\x1b[<u\x1b[>4;0m"
    )
    assert iterm_output.raw == (
        "\x1b[>4;0m\x1b[>5u\x1b[<u\x1b[>4;0m"
    )


def test_keyboard_mode_matches_codex_wsl_vscode_disable_policy(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        keyboard_adapter,
        "_query_windows_term_program",
        lambda: "vscode",
    )
    disabled_output = _RecordingOutput()
    disabled_mode = TerminalKeyboardMode(
        disabled_output,
        TerminalIdentity(TerminalKind.UNKNOWN, "unknown"),
        {"WSL_DISTRO_NAME": "Ubuntu"},
    )
    disabled_mode.enable()

    forced_output = _RecordingOutput()
    forced_mode = TerminalKeyboardMode(
        forced_output,
        TerminalIdentity(TerminalKind.UNKNOWN, "unknown"),
        {
            "WSL_DISTRO_NAME": "Ubuntu",
            "MIND_TUI_DISABLE_KEYBOARD_ENHANCEMENT": "false",
        },
    )
    forced_mode.enable()

    assert disabled_output.raw == ""
    assert forced_output.raw == "\x1b[>4;0m\x1b[>7u"
    forced_mode.restore()


def test_tmux_csi_u_mode_enables_modify_other_keys() -> None:
    output = _RecordingOutput()
    mode = TerminalKeyboardMode(
        output,
        TerminalIdentity(
            TerminalKind.WEZTERM,
            "WezTerm",
            multiplexer=TerminalKind.TMUX,
        ),
        {},
        tmux_format_probe=lambda: "csi-u",
    )

    with mode.active():
        assert mode.enabled
        with mode.active():
            assert mode.enabled
    assert not mode.enabled

    enable = "\x1b[>4;0m\x1b[>7u\x1b[>4;2m"
    restore = "\x1b[<u\x1b[>4;0m"
    assert output.raw == f"{enable}{restore}"


def test_input_adapter_balances_raw_and_cooked_keyboard_modes() -> None:
    output = _RecordingOutput()
    source = _BatchInput(())
    adapter = TerminalKeyboardInputAdapter(source, None, {})
    adapter.bind_terminal_output(
        output,
        TerminalIdentity(TerminalKind.WEZTERM, "WezTerm"),
    )

    with adapter.raw_mode():
        with adapter.cooked_mode():
            pass

    enable = "\x1b[>4;0m\x1b[>7u"
    restore = "\x1b[<u\x1b[>4;0m"
    assert output.raw == f"{enable}{restore}{enable}{restore}"


def test_keyboard_mode_freezes_terminal_probe_before_cooked_resume() -> None:
    output = _RecordingOutput()
    probes: list[str] = []

    def probe_tmux_format() -> str:
        probes.append("probe")
        return "csi-u"

    mode = TerminalKeyboardMode(
        output,
        TerminalIdentity(
            TerminalKind.WEZTERM,
            "WezTerm",
            multiplexer=TerminalKind.TMUX,
        ),
        {},
        tmux_format_probe=probe_tmux_format,
    )

    with mode.active():
        with mode.suspended():
            pass

    assert probes == ["probe"]
