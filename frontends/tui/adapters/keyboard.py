# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import os
import platform
import signal
import subprocess
import sys
import typing
from dataclasses import dataclass

from prompt_toolkit.application import in_terminal
from prompt_toolkit.input.base import Input
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.output.vt100 import Vt100_Output

from frontends.terminal.identity import TerminalIdentity
from frontends.terminal.identity import TerminalKind
from frontends.tui.contracts.keyboard import enhanced_key_token

_MAX_CSI_SEQUENCE_LENGTH: typing.Final[int] = 96

_DISABLE_ENV_VAR: typing.Final[str] = (
    "MIND_TUI_DISABLE_KEYBOARD_ENHANCEMENT"
)

_CSI_NAMED_KEYS: typing.Final[typing.Mapping[str, str]] = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "F": "end",
    "H": "home",
}
_CSI_TILDE_KEYS: typing.Final[typing.Mapping[int, str]] = {
    1: "home",
    2: "insert",
    3: "delete",
    4: "end",
    5: "page-up",
    6: "page-down",
    7: "home",
    8: "end",
    11: "f1",
    12: "f2",
    13: "f3",
    14: "f4",
    15: "f5",
    17: "f6",
    18: "f7",
    19: "f8",
    20: "f9",
    21: "f10",
    23: "f11",
    24: "f12",
    25: "f13",
    26: "f14",
    28: "f15",
    29: "f16",
    31: "f17",
    32: "f18",
    33: "f19",
    34: "f20",
}


def _no_suspend_surface() -> bool:
    """返回没有需要在进程恢复后重建的终端表面。"""
    return False


def _ignore_suspend_surface(_restore: bool) -> None:
    """忽略没有绑定前端生命周期的终端表面恢复。"""

_KITTY_FUNCTIONAL_KEYS: typing.Final[typing.Mapping[int, str]] = {
    57376 + offset: f"f{13 + offset}"
    for offset in range(12)
} | {
    57414: "enter",
    57417: "left",
    57418: "right",
    57419: "up",
    57420: "down",
    57421: "page-up",
    57422: "page-down",
    57423: "home",
    57424: "end",
    57425: "insert",
    57426: "delete",
}

_PLAIN_KEYS: typing.Final[typing.Mapping[str, Keys]] = {
    "esc": Keys.Escape,
    "enter": Keys.ControlM,
    "tab": Keys.ControlI,
    "backspace": Keys.ControlH,
    "delete": Keys.Delete,
    "insert": Keys.Insert,
    "up": Keys.Up,
    "down": Keys.Down,
    "left": Keys.Left,
    "right": Keys.Right,
    "home": Keys.Home,
    "end": Keys.End,
    "page-up": Keys.PageUp,
    "page-down": Keys.PageDown,
}


@dataclass(frozen=True, slots=True)
class _DecodedKeyEvent:
    """保存一个已校验的终端增强按键事实。"""
    key_name: str
    modifiers: frozenset[str]
    event_kind: int
    shifted_key: str | None = None


class EnhancedVt100Parser(Vt100Parser):
    """在 Prompt Toolkit VT 解析前消费 Kitty CSI-u 按键事件。"""

    def __init__(
        self,
        feed_key_callback: typing.Callable[[KeyPress], None],
    ) -> None:
        super().__init__(feed_key_callback)
        self._enhanced_pending = ""

    def feed(self, data: str) -> None:
        """增量解析增强事件，并把普通 VT 输入交还基础解析器。"""
        if self._in_bracketed_paste:
            super().feed(data)
            return None

        pending = f"{self._enhanced_pending}{data}"
        self._enhanced_pending = ""
        cursor = 0
        while cursor < len(pending):
            if self._in_bracketed_paste:
                super().feed(pending[cursor:])
                return None
            start = pending.find("\x1b[", cursor)
            if start < 0:
                tail = pending[cursor:]
                if tail.endswith("\x1b"):
                    super().feed(tail[:-1])
                    self._enhanced_pending = "\x1b"
                    return None
                super().feed(tail)
                return None
            if start > cursor:
                super().feed(pending[cursor:start])

            candidate = pending[start:]
            sequence_length = _csi_sequence_length(candidate)
            if sequence_length is None:
                if len(candidate) <= _MAX_CSI_SEQUENCE_LENGTH:
                    self._enhanced_pending = candidate
                    return None
                super().feed(candidate[0])
                cursor = start + 1
                continue

            sequence = candidate[:sequence_length]
            decoded = _decode_enhanced_sequence(sequence)
            if decoded is None:
                super().feed(sequence)
            else:
                for key_press in _key_presses(decoded):
                    self.feed_key_callback(key_press)
            cursor = start + sequence_length

    def flush(self) -> None:
        """把未组成增强事件的残留交还基础 VT 解析器并冲刷。"""
        if self._enhanced_pending:
            super().feed(self._enhanced_pending)
            self._enhanced_pending = ""
        super().flush()


class TerminalKeyboardMode:
    """拥有单个 TUI raw-mode 区间的增强键盘终端状态。"""

    def __init__(
        self,
        output: Output,
        identity: TerminalIdentity,
        environ: typing.Mapping[str, str],
        *,
        tmux_format_probe: typing.Callable[[], str | None] | None = None,
    ) -> None:
        self._output = output
        self._identity = identity
        self._environ = dict(environ)
        self._tmux_format_probe = (
            tmux_format_probe or _query_tmux_extended_keys_format
        )
        self._active = False
        self._enable_sequence: str | None = None
        self._mode_resolved = False

    @property
    def enabled(self) -> bool:
        """返回增强键盘模式当前是否由本实例持有。"""
        return self._active

    def enable(self) -> None:
        """按 Codex 终端策略启用增强按键报告。"""
        if self._active:
            return None
        sequence = self._resolve_enable_sequence()
        if not sequence:
            return None

        if self._write(sequence):
            self._active = True

    def _resolve_enable_sequence(self) -> str:
        """冻结当前 TUI 会话使用的终端增强模式序列。"""
        if self._mode_resolved:
            return self._enable_sequence or ""
        self._mode_resolved = True
        if not self._supported_output():
            return ""
        if _keyboard_enhancement_disabled(self._identity, self._environ):
            return ""

        tmux_format = (
            self._tmux_format_probe()
            if self._identity.multiplexer is TerminalKind.TMUX
            else None
        )
        flags = 5 if (
            self._identity.kind in {TerminalKind.ITERM2, TerminalKind.GHOSTTY}
            or tmux_format == "xterm"
        ) else 7
        sequence = f"\x1b[>4;0m\x1b[>{flags}u"
        if tmux_format == "csi-u":
            sequence += "\x1b[>4;2m"
        self._enable_sequence = sequence
        return sequence

    def restore(self) -> None:
        """幂等恢复进入 raw mode 前的键盘报告状态。"""
        if not self._active:
            return None
        self._write("\x1b[<u\x1b[>4;0m")
        self._active = False

    @contextlib.contextmanager
    def active(self) -> typing.Iterator[None]:
        """在一个 raw-mode 生命周期内成对启用并恢复终端状态。"""
        already_active = self._active
        self.enable()
        try:
            yield
        finally:
            if not already_active:
                self.restore()

    @contextlib.contextmanager
    def suspended(self) -> typing.Iterator[None]:
        """在外部 cooked-mode 程序拥有终端期间暂停增强报告。"""
        was_active = self._active
        if was_active:
            self.restore()
        try:
            yield
        finally:
            if was_active:
                self.enable()

    def _supported_output(self) -> bool:
        output = self._output
        if isinstance(output, (DummyOutput, PlainTextOutput)):
            return False
        if sys.platform != "win32":
            return True
        return bool(
            isinstance(output, Vt100_Output)
            or hasattr(output, "vt100_output")
        )

    def _write(self, sequence: str) -> bool:
        try:
            self._output.write_raw(sequence)
            self._output.flush()
        except (AttributeError, OSError, RuntimeError, ValueError):
            return False
        return True


class TerminalKeyboardInputAdapter(Input):
    """组合增强 VT 解析与终端键盘模式的完整输入生命周期。"""

    def __init__(
        self,
        input_obj: Input,
        parser: EnhancedVt100Parser | None,
        environ: typing.Mapping[str, str],
    ) -> None:
        self._input = input_obj
        self._parser = parser
        self._environ = dict(environ)
        self._mode: TerminalKeyboardMode | None = None
        self._suspend_task: asyncio.Task[None] | None = None
        self._prepare_suspend: typing.Callable[[], bool] = _no_suspend_surface
        self._restore_suspend: typing.Callable[[bool], None] = (
            _ignore_suspend_surface
        )

    @property
    def closed(self) -> bool:
        """返回被包装输入是否已经关闭。"""
        return self._input.closed

    @property
    def handle(self) -> int | None:
        """返回可选的 Windows Console handle。"""
        handle = getattr(self._input, "handle", None)
        value = getattr(handle, "value", handle)
        return value if isinstance(value, int) else None

    def bind_terminal_output(
        self,
        output: Output,
        identity: TerminalIdentity,
    ) -> None:
        """在 Application 输出确定后绑定同生命周期的终端模式。"""
        self._mode = TerminalKeyboardMode(output, identity, self._environ)

    def bind_terminal_suspend_lifecycle(
        self,
        prepare: typing.Callable[[], bool],
        restore: typing.Callable[[bool], None],
    ) -> None:
        """绑定 job control 暂离和恢复前端终端表面的回调。"""
        self._prepare_suspend = prepare
        self._restore_suspend = restore

    def replay(self, data: bytes, encoding: str) -> None:
        """把启动探测期间读取的非探测输入交还增强解析器。"""
        if self._parser is None:
            return None
        self._parser.feed(data.decode(encoding, errors="replace"))

    def fileno(self) -> int:
        """返回被包装输入的文件描述符。"""
        return self._input.fileno()

    def typeahead_hash(self) -> str:
        """复用被包装输入的 typeahead 身份。"""
        return self._input.typeahead_hash()

    def read_keys(self) -> list[KeyPress]:
        """读取已经过增强协议规范化的按键。"""
        return self._consume_job_control(self._input.read_keys())

    def flush_keys(self) -> list[KeyPress]:
        """冲刷增强协议和底层输入的残留按键。"""
        return self._consume_job_control(self._input.flush_keys())

    def flush(self) -> None:
        """把事件循环 flush 请求委托给底层输入。"""
        self._input.flush()

    @contextlib.contextmanager
    def raw_mode(self) -> typing.Iterator[None]:
        """在 raw mode 内成对持有增强键盘终端状态。"""
        with self._input.raw_mode():
            mode = self._mode
            if mode is None:
                yield
            else:
                with mode.active():
                    yield

    @contextlib.contextmanager
    def cooked_mode(self) -> typing.Iterator[None]:
        """外部程序使用终端时暂停增强键盘报告。"""
        mode = self._mode
        if mode is None:
            with self._input.cooked_mode():
                yield
            return
        with mode.suspended():
            with self._input.cooked_mode():
                yield

    def attach(
        self,
        input_ready_callback: typing.Callable[[], None],
    ) -> typing.ContextManager[None]:
        """把事件循环输入回调挂接到底层输入。"""
        return self._input.attach(input_ready_callback)

    def detach(self) -> typing.ContextManager[None]:
        """复用底层输入的事件循环解除挂接边界。"""
        return self._input.detach()

    def close(self) -> None:
        """恢复终端模式并关闭底层输入。"""
        suspend_task = self._suspend_task
        if suspend_task is not None and not suspend_task.done():
            suspend_task.cancel()
        self._suspend_task = None
        if self._mode is not None:
            self._mode.restore()
        self._input.close()

    def _consume_job_control(
        self,
        keys: typing.Iterable[KeyPress],
    ) -> list[KeyPress]:
        """消费 Ctrl+Z，并在支持作业控制的平台请求一次进程挂起。"""
        retained: list[KeyPress] = []
        suspend_requested = False
        for key_press in keys:
            if not _is_suspend_key(key_press):
                retained.append(key_press)
                continue
            suspend_requested = True
        if suspend_requested:
            self._request_suspend()
        return retained

    def _request_suspend(self) -> None:
        """幂等调度单个 POSIX 挂起与恢复生命周期。"""
        if not _job_control_supported():
            return None
        current = self._suspend_task
        if current is not None and not current.done():
            return None
        self._suspend_task = asyncio.get_running_loop().create_task(
            self._suspend_to_background()
        )

    async def _suspend_to_background(self) -> None:
        """离开 raw mode，挂起当前进程组并在恢复后重建终端。"""
        restore_surface = self._prepare_suspend()
        surface_restored = False
        try:
            async with in_terminal():
                try:
                    _stop_current_process_group()
                    _flush_terminal_input_buffer(self.fileno())
                finally:
                    surface_restored = True
                    self._restore_suspend(restore_surface)
        finally:
            if not surface_restored:
                self._restore_suspend(restore_surface)
            self._suspend_task = None


def install_enhanced_vt_parser(
    input_obj: Input,
) -> EnhancedVt100Parser | None:
    """在 Prompt Toolkit VT 输入上安装可识别 CSI-u 的解析器。"""
    parser = getattr(input_obj, "vt100_parser", None)
    if not isinstance(parser, Vt100Parser):
        return None
    enhanced = EnhancedVt100Parser(parser.feed_key_callback)
    setattr(input_obj, "vt100_parser", enhanced)
    return enhanced


def replay_tui_terminal_input(
    input_obj: Input,
    data: bytes,
    encoding: str,
) -> None:
    """把启动探测保留的输入回放到 TUI 输入 adapter。"""
    if isinstance(input_obj, TerminalKeyboardInputAdapter):
        input_obj.replay(data, encoding)


def _is_suspend_key(key_press: KeyPress) -> bool:
    """判断规范化按键是否为传统或增强 Ctrl+Z。"""
    enhanced = enhanced_key_token("z", frozenset({"ctrl"}))
    return bool(
        key_press.key == Keys.ControlZ
        or (enhanced is not None and key_press.key == enhanced)
    )


def _job_control_supported() -> bool:
    """判断当前平台是否提供 POSIX 进程挂起信号。"""
    return isinstance(getattr(signal, "SIGTSTP", None), signal.Signals)


def _stop_current_process_group() -> None:
    """向当前 POSIX 进程组发送挂起信号。"""
    suspend_signal = getattr(signal, "SIGTSTP", None)
    if not isinstance(suspend_signal, signal.Signals):
        return None
    os.kill(0, suspend_signal)


def _flush_terminal_input_buffer(file_descriptor: int) -> None:
    """在 POSIX 进程恢复后丢弃挂起期间积压的终端输入。"""
    import termios

    termios.tcflush(file_descriptor, termios.TCIFLUSH)


def _csi_sequence_length(candidate: str) -> int | None:
    if not candidate.startswith("\x1b["):
        return 0
    if len(candidate) < 3:
        return None
    if not (candidate[2].isdigit() or candidate[2] in {"?", ">"}):
        return 1
    for index, char in enumerate(candidate[2:], start=2):
        if "@" <= char <= "~":
            return index + 1
    return None


def _decode_enhanced_sequence(
    sequence: str,
) -> _DecodedKeyEvent | tuple[()] | None:
    if len(sequence) < 4 or not sequence.startswith("\x1b["):
        return None
    final = sequence[-1]
    parameters = sequence[2:-1]
    if final == "u":
        if parameters.startswith("?"):
            return ()
        return _decode_csi_u(parameters)
    if final == "~" and parameters.startswith("27;"):
        return _decode_modify_other_keys(parameters)
    if final == "~" and ":" in parameters:
        return _decode_csi_tilde(parameters)
    if final in _CSI_NAMED_KEYS and ":" in parameters:
        return _decode_csi_named(final, parameters)
    return None


def _decode_csi_u(parameters: str) -> _DecodedKeyEvent | tuple[()]:
    fields = parameters.split(";")
    try:
        codepoints = fields[0].split(":")
        key_code = int(codepoints[0])
        modifiers, event_kind, supported = _parse_modifiers(
            fields[1] if len(fields) > 1 else "1"
        )
    except (IndexError, ValueError):
        return ()
    if not supported:
        return ()
    key_name = _key_name_from_codepoint(key_code)
    if key_name is None:
        return ()
    shifted_key = None
    if len(codepoints) > 1:
        try:
            shifted_key = chr(int(codepoints[1]))
        except (OverflowError, ValueError):
            shifted_key = None
    return _DecodedKeyEvent(
        key_name=key_name,
        modifiers=modifiers,
        event_kind=event_kind,
        shifted_key=shifted_key,
    )


def _decode_modify_other_keys(
    parameters: str,
) -> _DecodedKeyEvent | tuple[()]:
    fields = parameters.split(";")
    try:
        modifiers, event_kind, supported = _parse_modifiers(fields[1])
        key_name = _key_name_from_codepoint(int(fields[2]))
    except (IndexError, ValueError):
        return ()
    if not supported or key_name is None:
        return ()
    return _DecodedKeyEvent(key_name, modifiers, event_kind)


def _decode_csi_tilde(parameters: str) -> _DecodedKeyEvent | tuple[()]:
    fields = parameters.split(";")
    try:
        key_name = _CSI_TILDE_KEYS[int(fields[0])]
        modifiers, event_kind, supported = _parse_modifiers(fields[1])
    except (IndexError, KeyError, ValueError):
        return ()
    if not supported:
        return ()
    return _DecodedKeyEvent(key_name, modifiers, event_kind)


def _decode_csi_named(
    final: str,
    parameters: str,
) -> _DecodedKeyEvent | tuple[()]:
    fields = parameters.split(";")
    try:
        modifiers, event_kind, supported = _parse_modifiers(fields[1])
    except (IndexError, ValueError):
        return ()
    if not supported:
        return ()
    return _DecodedKeyEvent(
        _CSI_NAMED_KEYS[final],
        modifiers,
        event_kind,
    )


def _parse_modifiers(
    value: str,
) -> tuple[frozenset[str], int, bool]:
    modifier_text, separator, event_text = value.partition(":")
    modifier_mask = max(0, int(modifier_text) - 1)
    event_kind = int(event_text) if separator and event_text else 1
    unsupported = modifier_mask & (8 | 16 | 32)
    modifiers = frozenset(
        modifier
        for bit, modifier in ((1, "shift"), (2, "alt"), (4, "ctrl"))
        if modifier_mask & bit
    )
    return modifiers, event_kind, not bool(unsupported)


def _key_name_from_codepoint(codepoint: int) -> str | None:
    functional = _KITTY_FUNCTIONAL_KEYS.get(codepoint)
    if functional is not None:
        return functional
    special = {
        9: "tab",
        13: "enter",
        27: "esc",
        32: "space",
        127: "backspace",
    }.get(codepoint)
    if special is not None:
        return special
    try:
        return chr(codepoint)
    except (OverflowError, ValueError):
        return None


def _key_presses(
    decoded: _DecodedKeyEvent | tuple[()],
) -> tuple[KeyPress, ...]:
    if not isinstance(decoded, _DecodedKeyEvent):
        return ()
    if decoded.event_kind == 3:
        return ()
    if decoded.event_kind not in {1, 2}:
        return ()

    if not decoded.modifiers:
        key = _plain_key(decoded.key_name)
        return () if key is None else (key,)
    if (
        decoded.modifiers == frozenset({"shift"})
        and len(decoded.key_name) == 1
    ):
        text = decoded.shifted_key or (
            decoded.key_name.upper()
            if decoded.key_name.isalpha()
            else decoded.key_name
        )
        return (KeyPress(text, text),)

    token = enhanced_key_token(decoded.key_name, decoded.modifiers)
    return () if token is None else (KeyPress(token, ""),)


def _plain_key(key_name: str) -> KeyPress | None:
    named = _PLAIN_KEYS.get(key_name)
    if named is not None:
        return KeyPress(named, "")
    if key_name == "space":
        return KeyPress(" ", " ")
    if key_name.startswith("f") and key_name[1:].isdigit():
        try:
            return KeyPress(Keys(key_name), "")
        except ValueError:
            return None
    if len(key_name) == 1:
        return KeyPress(key_name, key_name)
    return None


def _keyboard_enhancement_disabled(
    identity: TerminalIdentity,
    environ: typing.Mapping[str, str],
) -> bool:
    explicit = _parse_bool(environ.get(_DISABLE_ENV_VAR))
    if explicit is not None:
        return explicit
    return bool(
        _running_in_wsl(environ)
        and _running_in_vscode_terminal(identity, environ)
    )


def _parse_bool(value: str | None) -> bool | None:
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    return None


def _running_in_wsl(environ: typing.Mapping[str, str]) -> bool:
    if environ.get("WSL_DISTRO_NAME") or environ.get("WSL_INTEROP"):
        return True
    return "microsoft" in platform.release().casefold()


def _running_in_vscode_terminal(
    identity: TerminalIdentity,
    environ: typing.Mapping[str, str],
) -> bool:
    if identity.kind is TerminalKind.VSCODE:
        return True
    term_program = str(environ.get("TERM_PROGRAM") or "").strip()
    if term_program.casefold() == "vscode":
        return True
    windows_term_program = _query_windows_term_program()
    return windows_term_program.casefold() == "vscode"


def _query_windows_term_program() -> str:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ("cmd.exe", "/d", "/s", "/c", "set TERM_PROGRAM"),
            capture_output=True,
            text=True,
            check=False,
            timeout=0.1,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip().casefold() == "term_program":
            return value.strip()
    return ""


def _query_tmux_extended_keys_format() -> str | None:
    if not os.environ.get("TMUX") and not os.environ.get("TMUX_PANE"):
        return None
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for arguments in (
        ("display-message", "-p", "#{extended-keys-format}"),
        ("show-options", "-gqv", "extended-keys-format"),
    ):
        try:
            result = subprocess.run(
                ("tmux", *arguments),
                capture_output=True,
                text=True,
                check=False,
                timeout=0.1,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        value = result.stdout.strip()
        if result.returncode == 0 and value:
            return value
    return None


if __name__ == '__main__':
    pass
