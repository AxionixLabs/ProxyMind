import codecs
import re
import threading
import time
import typing
import types
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pyte

from .contract import PtyKey
from .contract import PtySession
from .contract import TerminalSize
from .contract import spawn_pty


_DEFAULT_QUERY_TIMEOUT = 0.1
_DEFAULT_MAX_QUERY_BYTES = 64
_IDENTITY_ENVIRONMENT_KEYS = frozenset({
    "ALACRITTY_LOG",
    "ALACRITTY_SOCKET",
    "COLORTERM",
    "FORCE_COLOR",
    "GNOME_TERMINAL_SCREEN",
    "KITTY_WINDOW_ID",
    "KONSOLE_VERSION",
    "NO_COLOR",
    "TERM",
    "TERMINAL_EMULATOR",
    "TERM_PROGRAM",
    "TERM_PROGRAM_VERSION",
    "TMUX",
    "VTE_VERSION",
    "WEZTERM_EXECUTABLE",
    "WEZTERM_PANE",
    "WEZTERM_VERSION",
    "WT_SESSION",
    "ZELLIJ",
    "ZELLIJ_SESSION_NAME",
    "ZELLIJ_VERSION",
})


class TerminalQuery(Enum):
    """标识验收终端能够回答的查询。"""

    CURSOR_POSITION = "cursor_position"
    DEFAULT_FOREGROUND = "default_foreground"
    DEFAULT_BACKGROUND = "default_background"
    KEYBOARD_ENHANCEMENT = "keyboard_enhancement"
    PRIMARY_DEVICE_ATTRIBUTES = "primary_device_attributes"


class TerminalInputSource(Enum):
    """区分测试用户输入和终端自动响应。"""

    USER = "user"
    TERMINAL_RESPONSE = "terminal_response"


class TerminalMode(Enum):
    """标识需要跨帧观察的终端模式切换。"""

    KEYBOARD_ENHANCEMENT_ENABLED = "keyboard_enhancement_enabled"
    KEYBOARD_ENHANCEMENT_RESTORED = "keyboard_enhancement_restored"
    FOCUS_REPORTING_ENABLED = "focus_reporting_enabled"
    FOCUS_REPORTING_DISABLED = "focus_reporting_disabled"
    SYNCHRONIZED_OUTPUT_ENABLED = "synchronized_output_enabled"
    SYNCHRONIZED_OUTPUT_DISABLED = "synchronized_output_disabled"
    ALTERNATE_SCREEN_ENABLED = "alternate_screen_enabled"
    ALTERNATE_SCREEN_DISABLED = "alternate_screen_disabled"
    ALTERNATE_SCROLL_ENABLED = "alternate_scroll_enabled"
    ALTERNATE_SCROLL_DISABLED = "alternate_scroll_disabled"
    BRACKETED_PASTE_ENABLED = "bracketed_paste_enabled"
    BRACKETED_PASTE_DISABLED = "bracketed_paste_disabled"
    CURSOR_HIDDEN = "cursor_hidden"
    CURSOR_SHOWN = "cursor_shown"


@dataclass(frozen=True, slots=True)
class TerminalEnvironment:
    """定义单个验收场景的不可变终端身份与色深环境。"""

    term: str = "xterm-256color"
    colorterm: str = "truecolor"
    term_program: str = "WezTerm"
    term_program_version: str = "2026.1"
    wt_session: str | None = None
    no_color: bool = False

    def derive(self, base: typing.Mapping[str, str]) -> dict[str, str]:
        """从父环境派生不受宿主终端身份污染的子进程环境。"""
        environment = {
            key: value
            for key, value in base.items()
            if key not in _IDENTITY_ENVIRONMENT_KEYS
        }
        environment["TERM"] = self.term
        environment["COLORTERM"] = self.colorterm
        environment["TERM_PROGRAM"] = self.term_program
        environment["TERM_PROGRAM_VERSION"] = self.term_program_version
        if self.wt_session is not None:
            environment["WT_SESSION"] = self.wt_session
        if self.no_color:
            environment["NO_COLOR"] = "1"
        return environment


@dataclass(frozen=True, slots=True)
class TerminalReplyConfig:
    """定义终端探测响应以及有界解析预算。"""

    foreground: str | None = "rgb:eeee/eeee/eeee"
    background: str | None = "rgb:1111/1111/1111"
    cursor_row: int = 1
    cursor_column: int = 1
    keyboard_flags: int | None = 7
    primary_device_attributes: str | None = "64;1;2"
    max_query_bytes: int = _DEFAULT_MAX_QUERY_BYTES

    def __post_init__(self) -> None:
        if self.cursor_row <= 0 or self.cursor_column <= 0:
            raise ValueError("terminal cursor response must be one-based")
        if self.max_query_bytes < 16:
            raise ValueError("terminal query byte budget must be at least 16")


@dataclass(frozen=True, slots=True)
class TerminalQueryEvent:
    """记录一个完整查询及其可选响应。"""

    query: TerminalQuery
    response: bytes | None


@dataclass(frozen=True, slots=True)
class TerminalModeEvent:
    """记录一个完整终端模式控制序列。"""

    mode: TerminalMode
    sequence: bytes


@dataclass(frozen=True, slots=True)
class TerminalProtocolBatch:
    """汇总单次增量解析得到的查询和模式事件。"""

    queries: tuple[TerminalQueryEvent, ...]
    modes: tuple[TerminalModeEvent, ...]


@dataclass(frozen=True, slots=True)
class TerminalInputEvent:
    """记录按实际写入顺序送入 PTY 的输入。"""

    sequence: int
    source: TerminalInputSource
    data: bytes
    query: TerminalQuery | None = None


@dataclass(frozen=True, slots=True)
class TerminalCell:
    """保存单个 VT Screen 单元格的可断言语义。"""

    data: str
    foreground: str
    background: str
    bold: bool
    italics: bool
    underline: bool
    strikethrough: bool
    reverse: bool
    blink: bool


@dataclass(frozen=True, slots=True)
class TerminalCursor:
    """保存零基 Screen 光标位置与可见性。"""

    row: int
    column: int
    hidden: bool


@dataclass(frozen=True, slots=True)
class TerminalSnapshot:
    """保存某一稳定时刻的可见 Screen 与 scrollback。"""

    visible_lines: tuple[str, ...]
    scrollback_lines: tuple[str, ...]
    cursor: TerminalCursor

    @property
    def visible_text(self) -> str:
        """返回保留行边界的可见文本。"""
        return "\n".join(self.visible_lines)

    @property
    def scrollback_text(self) -> str:
        """返回保留行边界的历史文本。"""
        return "\n".join(self.scrollback_lines)


@dataclass(frozen=True, slots=True)
class TerminalDiagnostics:
    """保存可区分原始输出和解析结果的终端诊断。"""

    raw_output: bytes
    screen: TerminalSnapshot


class TerminalQueryResponder:
    """以固定内存上限识别并回答终端查询。"""

    _patterns = (
        (b"\x1b[6n", TerminalQuery.CURSOR_POSITION),
        (b"\x1b]10;?\x1b\\", TerminalQuery.DEFAULT_FOREGROUND),
        (b"\x1b]10;?\x07", TerminalQuery.DEFAULT_FOREGROUND),
        (b"\x1b]11;?\x1b\\", TerminalQuery.DEFAULT_BACKGROUND),
        (b"\x1b]11;?\x07", TerminalQuery.DEFAULT_BACKGROUND),
        (b"\x1b[?u", TerminalQuery.KEYBOARD_ENHANCEMENT),
        (b"\x1b[c", TerminalQuery.PRIMARY_DEVICE_ATTRIBUTES),
    )
    _fixed_mode_patterns = (
        (b"\x1b[<u", TerminalMode.KEYBOARD_ENHANCEMENT_RESTORED),
        (b"\x1b[?1004h", TerminalMode.FOCUS_REPORTING_ENABLED),
        (b"\x1b[?1004l", TerminalMode.FOCUS_REPORTING_DISABLED),
        (b"\x1b[?2026h", TerminalMode.SYNCHRONIZED_OUTPUT_ENABLED),
        (b"\x1b[?2026l", TerminalMode.SYNCHRONIZED_OUTPUT_DISABLED),
        (b"\x1b[?1049h", TerminalMode.ALTERNATE_SCREEN_ENABLED),
        (b"\x1b[?1049l", TerminalMode.ALTERNATE_SCREEN_DISABLED),
        (b"\x1b[?1007h", TerminalMode.ALTERNATE_SCROLL_ENABLED),
        (b"\x1b[?1007l", TerminalMode.ALTERNATE_SCROLL_DISABLED),
        (b"\x1b[?2004h", TerminalMode.BRACKETED_PASTE_ENABLED),
        (b"\x1b[?2004l", TerminalMode.BRACKETED_PASTE_DISABLED),
        (b"\x1b[?25l", TerminalMode.CURSOR_HIDDEN),
        (b"\x1b[?25h", TerminalMode.CURSOR_SHOWN),
    )
    _keyboard_enable_pattern = re.compile(rb"\x1b\[>[0-9;]{1,32}u")

    def __init__(self, config: TerminalReplyConfig) -> None:
        self._config = config
        self._pending = bytearray()
        self._mode_pending = bytearray()

    @property
    def buffered_bytes(self) -> int:
        """返回当前保留的未完成控制序列字节数。"""
        return max(len(self._pending), len(self._mode_pending))

    def feed(self, data: bytes) -> TerminalProtocolBatch:
        """消费新增输出并返回完整查询与模式事件。"""
        self._pending.extend(data)
        query_events: list[TerminalQueryEvent] = []
        while self._pending:
            match = self._next_match()
            if match is None:
                excess = len(self._pending) - self._config.max_query_bytes
                if excess > 0:
                    del self._pending[:excess]
                break
            start, pattern, query = match
            del self._pending[:start + len(pattern)]
            query_events.append(TerminalQueryEvent(query, self._response(query)))
        return TerminalProtocolBatch(
            queries=tuple(query_events),
            modes=self._feed_modes(data),
        )

    def _next_match(self) -> tuple[int, bytes, TerminalQuery] | None:
        matches: list[tuple[int, bytes, TerminalQuery]] = []
        pending = bytes(self._pending)
        for pattern, query in self._patterns:
            index = pending.find(pattern)
            if index >= 0:
                matches.append((index, pattern, query))
        if not matches:
            return None
        return min(matches, key=lambda item: item[0])

    def _response(self, query: TerminalQuery) -> bytes | None:
        if query is TerminalQuery.CURSOR_POSITION:
            return (
                f"\x1b[{self._config.cursor_row};"
                f"{self._config.cursor_column}R"
            ).encode("ascii")
        if query is TerminalQuery.DEFAULT_FOREGROUND:
            return self._osc_response(10, self._config.foreground)
        if query is TerminalQuery.DEFAULT_BACKGROUND:
            return self._osc_response(11, self._config.background)
        if query is TerminalQuery.KEYBOARD_ENHANCEMENT:
            flags = self._config.keyboard_flags
            if flags is None:
                return None
            return f"\x1b[?{flags}u".encode("ascii")
        attributes = self._config.primary_device_attributes
        if attributes is None:
            return None
        return f"\x1b[?{attributes}c".encode("ascii")

    def _feed_modes(self, data: bytes) -> tuple[TerminalModeEvent, ...]:
        self._mode_pending.extend(data)
        events: list[TerminalModeEvent] = []
        while self._mode_pending:
            match = self._next_mode_match()
            if match is None:
                excess = len(self._mode_pending) - self._config.max_query_bytes
                if excess > 0:
                    del self._mode_pending[:excess]
                break
            start, end, mode = match
            sequence = bytes(self._mode_pending[start:end])
            del self._mode_pending[:end]
            events.append(TerminalModeEvent(mode, sequence))
        return tuple(events)

    def _next_mode_match(self) -> tuple[int, int, TerminalMode] | None:
        pending = bytes(self._mode_pending)
        matches: list[tuple[int, int, TerminalMode]] = []
        for pattern, mode in self._fixed_mode_patterns:
            index = pending.find(pattern)
            if index >= 0:
                matches.append((index, index + len(pattern), mode))
        keyboard_match = self._keyboard_enable_pattern.search(pending)
        if keyboard_match is not None:
            matches.append((
                keyboard_match.start(),
                keyboard_match.end(),
                TerminalMode.KEYBOARD_ENHANCEMENT_ENABLED,
            ))
        if not matches:
            return None
        return min(matches, key=lambda item: item[0])

    @staticmethod
    def _osc_response(slot: int, value: str | None) -> bytes | None:
        if value is None:
            return None
        return f"\x1b]{slot};{value}\x1b\\".encode("ascii")


class TerminalScreen:
    """把真实 PTY 输出投影为可等待、可检查的 VT Screen。"""

    def __init__(self, size: TerminalSize, history: int = 2000) -> None:
        if history <= 0:
            raise ValueError("terminal history must be positive")
        self._condition = threading.Condition(threading.RLock())
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._screen = pyte.HistoryScreen(
            size.columns,
            size.rows,
            history=history,
        )
        self._stream = pyte.Stream(self._screen, strict=False)

    def feed(self, data: bytes) -> None:
        """增量解析原始 UTF-8 与 VT 控制序列。"""
        text = self._decoder.decode(data)
        if not text:
            return
        with self._condition:
            self._stream.feed(text)
            self._condition.notify_all()

    def resize(self, size: TerminalSize) -> None:
        """让 Screen oracle 与原生终端使用相同行列。"""
        with self._condition:
            self._screen.resize(lines=size.rows, columns=size.columns)
            self._condition.notify_all()

    def cell(self, row: int, column: int) -> TerminalCell:
        """返回指定零基坐标的稳定单元格快照。"""
        with self._condition:
            if not 0 <= row < self._screen.lines:
                raise IndexError("terminal cell row is outside the screen")
            if not 0 <= column < self._screen.columns:
                raise IndexError("terminal cell column is outside the screen")
            value = self._screen.buffer[row][column]
            return TerminalCell(
                data=value.data,
                foreground=value.fg,
                background=value.bg,
                bold=value.bold,
                italics=value.italics,
                underline=value.underscore,
                strikethrough=value.strikethrough,
                reverse=value.reverse,
                blink=value.blink,
            )

    def snapshot(self) -> TerminalSnapshot:
        """截取当前可见内容、scrollback 和光标。"""
        with self._condition:
            visible_lines = tuple(self._screen.display)
            scrollback_lines = tuple(
                "".join(
                    line[column].data
                    for column in range(self._screen.columns)
                ).rstrip()
                for line in self._screen.history.top
            )
            cursor = TerminalCursor(
                row=self._screen.cursor.y,
                column=self._screen.cursor.x,
                hidden=self._screen.cursor.hidden,
            )
            return TerminalSnapshot(
                visible_lines=visible_lines,
                scrollback_lines=scrollback_lines,
                cursor=cursor,
            )

    def wait_for_text(
        self,
        expected: str,
        timeout: float = 5.0,
    ) -> TerminalSnapshot:
        """等待可见区或 scrollback 出现目标文本后再取快照。"""
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                snapshot = self.snapshot()
                if (
                    expected in snapshot.visible_text
                    or expected in snapshot.scrollback_text
                    or expected in "".join(snapshot.visible_lines)
                    or expected in "".join(snapshot.scrollback_lines)
                ):
                    return snapshot
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"timed out waiting for terminal screen text: {expected!r}; "
                        f"visible={snapshot.visible_text!r}; "
                        f"scrollback={snapshot.scrollback_text!r}"
                    )
                self._condition.wait(remaining)


class TerminalHarness:
    """组合 PTY、查询响应、输入日志与 VT Screen 生命周期。"""

    def __init__(
        self,
        session: PtySession,
        *,
        size: TerminalSize,
        replies: TerminalReplyConfig,
        failure_artifact_directory: Path | None = None,
    ) -> None:
        self.session = session
        self.screen = TerminalScreen(size)
        self._failure_artifact_directory = failure_artifact_directory
        self._responder = TerminalQueryResponder(replies)
        self._condition = threading.Condition()
        self._input_lock = threading.Lock()
        self._query_events: list[TerminalQueryEvent] = []
        self._mode_events: list[TerminalModeEvent] = []
        self._input_events: list[TerminalInputEvent] = []
        self._monitor_error: Exception | None = None
        self._closing = threading.Event()
        self._monitor = threading.Thread(
            target=self._monitor_output,
            name=f"terminal-monitor-{session.pid}",
            daemon=True,
        )
        self._monitor.start()

    @property
    def query_events(self) -> tuple[TerminalQueryEvent, ...]:
        """返回按输出顺序识别的终端查询。"""
        with self._condition:
            return tuple(self._query_events)

    @property
    def input_events(self) -> tuple[TerminalInputEvent, ...]:
        """返回按实际写入顺序记录的用户输入与响应。"""
        with self._condition:
            return tuple(self._input_events)

    @property
    def mode_events(self) -> tuple[TerminalModeEvent, ...]:
        """返回按输出顺序识别的终端模式切换。"""
        with self._condition:
            return tuple(self._mode_events)

    @property
    def buffered_query_bytes(self) -> int:
        """返回有界查询解析器当前缓存量。"""
        with self._condition:
            return self._responder.buffered_bytes

    def diagnostics(self) -> TerminalDiagnostics:
        """截取当前 raw output 与最终 Screen 诊断。"""
        return TerminalDiagnostics(
            raw_output=self.session.output(),
            screen=self.screen.snapshot(),
        )

    def save_failure_artifacts(self, directory: Path) -> None:
        """把 raw output 与 Screen 分别写入失败场景目录。"""
        directory.mkdir(parents=True, exist_ok=True)
        diagnostics = self.diagnostics()
        directory.joinpath("raw-output.bin").write_bytes(
            diagnostics.raw_output
        )
        screen_text = (
            "[scrollback]\n"
            f"{diagnostics.screen.scrollback_text}\n"
            "[screen]\n"
            f"{diagnostics.screen.visible_text}\n"
            "[cursor]\n"
            f"row={diagnostics.screen.cursor.row} "
            f"column={diagnostics.screen.cursor.column} "
            f"hidden={diagnostics.screen.cursor.hidden}\n"
        )
        directory.joinpath("screen.txt").write_text(
            screen_text,
            encoding="utf-8",
        )

    def write_user(self, data: bytes) -> None:
        """写入并记录用户原始输入。"""
        self._write_input(data, TerminalInputSource.USER, None)

    def write_user_text(self, value: str) -> None:
        """以 UTF-8 写入并记录用户文本。"""
        self.write_user(value.encode("utf-8"))

    def send_key(self, key: PtyKey) -> None:
        """写入并记录稳定终端按键。"""
        with self._input_lock:
            self.session.send_key(key)
            self._append_input_event(TerminalInputSource.USER, key.value, None)

    def resize(self, size: TerminalSize) -> None:
        """同步调整原生 PTY 与 Screen oracle。"""
        self.session.resize(size)
        self.screen.resize(size)

    def wait_for_queries(
        self,
        queries: typing.Iterable[TerminalQuery],
        timeout: float = _DEFAULT_QUERY_TIMEOUT,
    ) -> tuple[TerminalQueryEvent, ...]:
        """在一个共享截止时间内等待每类查询至少出现一次。"""
        expected = frozenset(queries)
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                self._raise_monitor_error()
                seen = {event.query for event in self._query_events}
                if expected <= seen:
                    return tuple(self._query_events)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    missing = sorted(
                        query.value for query in expected - seen
                    )
                    raise TimeoutError(
                        f"timed out waiting for terminal queries: {missing}; "
                        f"raw={self.session.output()!r}; "
                        f"screen={self.screen.snapshot().visible_text!r}"
                    )
                self._condition.wait(remaining)

    def wait_for_modes(
        self,
        modes: typing.Iterable[TerminalMode],
        timeout: float = _DEFAULT_QUERY_TIMEOUT,
    ) -> tuple[TerminalModeEvent, ...]:
        """在一个共享截止时间内等待每类模式至少出现一次。"""
        expected = frozenset(modes)
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                self._raise_monitor_error()
                seen = {event.mode for event in self._mode_events}
                if expected <= seen:
                    return tuple(self._mode_events)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    missing = sorted(mode.value for mode in expected - seen)
                    raise TimeoutError(
                        f"timed out waiting for terminal modes: {missing}; "
                        f"raw={self.session.output()!r}"
                    )
                self._condition.wait(remaining)

    def wait_for_screen_text(
        self,
        expected: str,
        timeout: float = 5.0,
    ) -> TerminalSnapshot:
        """等待稳定 Screen 条件并传播 monitor 失败。"""
        try:
            return self.screen.wait_for_text(expected, timeout)
        except TimeoutError:
            with self._condition:
                self._raise_monitor_error()
            raise

    def wait_for_exit(self, timeout: float = 5.0) -> int:
        """等待根进程、原始输出和 Screen monitor 全部收敛。"""
        exit_code = self.session.wait_for_exit(timeout)
        self._monitor.join(timeout)
        if self._monitor.is_alive():
            raise TimeoutError("terminal output monitor did not stop")
        with self._condition:
            self._raise_monitor_error()
        return exit_code

    def close(self) -> None:
        """幂等关闭 PTY 并回收终端 monitor。"""
        if self._closing.is_set():
            return
        self._closing.set()
        try:
            self.session.close()
        finally:
            self._monitor.join(2.0)
        if self._monitor.is_alive():
            raise TimeoutError("terminal output monitor did not stop")
        with self._condition:
            self._raise_monitor_error()

    def __enter__(self) -> "TerminalHarness":
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        try:
            if exception is not None and self._failure_artifact_directory is not None:
                self.save_failure_artifacts(self._failure_artifact_directory)
        finally:
            self.close()

    def _monitor_output(self) -> None:
        offset = 0
        try:
            while True:
                try:
                    output, closed = self.session.wait_for_output_change(
                        offset,
                        _DEFAULT_QUERY_TIMEOUT,
                    )
                except TimeoutError:
                    if self._closing.is_set():
                        continue
                    continue
                chunk = output[offset:]
                offset = len(output)
                if chunk:
                    self.screen.feed(chunk)
                    with self._condition:
                        batch = self._responder.feed(chunk)
                    for event in batch.queries:
                        if event.response is not None and not self._closing.is_set():
                            try:
                                self._write_input(
                                    event.response,
                                    TerminalInputSource.TERMINAL_RESPONSE,
                                    event.query,
                                )
                            except (OSError, RuntimeError):
                                if not self._closing.is_set():
                                    raise
                        with self._condition:
                            self._query_events.append(event)
                            self._condition.notify_all()
                    if batch.modes:
                        with self._condition:
                            self._mode_events.extend(batch.modes)
                            self._condition.notify_all()
                if closed:
                    return
        except Exception as exc:
            with self._condition:
                self._monitor_error = exc
                self._condition.notify_all()

    def _write_input(
        self,
        data: bytes,
        source: TerminalInputSource,
        query: TerminalQuery | None,
    ) -> None:
        with self._input_lock:
            self.session.write(data)
            self._append_input_event(source, data, query)

    def _append_input_event(
        self,
        source: TerminalInputSource,
        data: bytes,
        query: TerminalQuery | None,
    ) -> None:
        with self._condition:
            event = TerminalInputEvent(
                sequence=len(self._input_events),
                source=source,
                data=data,
                query=query,
            )
            self._input_events.append(event)
            self._condition.notify_all()

    def _raise_monitor_error(self) -> None:
        error = self._monitor_error
        if error is not None:
            raise RuntimeError("terminal output monitor failed") from error


def spawn_terminal(
    argv: typing.Sequence[str],
    *,
    cwd: Path,
    env: typing.Mapping[str, str],
    size: TerminalSize = TerminalSize(),
    terminal: TerminalEnvironment = TerminalEnvironment(),
    replies: TerminalReplyConfig = TerminalReplyConfig(),
    failure_artifact_directory: Path | None = None,
) -> TerminalHarness:
    """以固定终端环境创建带协议响应和 Screen oracle 的 PTY。"""
    session = spawn_pty(
        argv,
        cwd=cwd,
        env=terminal.derive(env),
        size=size,
    )
    return TerminalHarness(
        session,
        size=size,
        replies=replies,
        failure_artifact_directory=failure_artifact_directory,
    )


if __name__ == '__main__':
    pass
