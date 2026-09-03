# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import select
import sys
import threading
import time
import typing
from dataclasses import dataclass

from .color_support import (
    DEGRADED_COLOR_SUPPORT,
    TerminalColorLevel,
    TerminalColorSupport,
    detect_terminal_color_support,
    stream_is_tty,
)
from .identity import (
    TerminalIdentity,
    TerminalKind,
    TmuxProbe,
    detect_terminal_identity,
)

RgbColor: typing.TypeAlias = tuple[int, int, int]

TERMINAL_QUERY_TIMEOUT_SEC = 0.1


HYPERLINK_TERMINALS = frozenset({
    TerminalKind.VSCODE,
    TerminalKind.VTE,
    TerminalKind.ZELLIJ,
    TerminalKind.GHOSTTY,
    TerminalKind.KITTY,
    TerminalKind.ITERM2,
    TerminalKind.WEZTERM,
    TerminalKind.ALACRITTY,
    TerminalKind.KONSOLE,
    TerminalKind.WINDOWS_TERMINAL,
})


@dataclass(frozen=True)
class TerminalTheme:
    """保存终端报告的默认颜色及可选语法作用域表面。"""
    foreground: RgbColor | None = None
    background: RgbColor | None = None
    scope_backgrounds: tuple[tuple[str, RgbColor], ...] = ()


@dataclass(frozen=True)
class TerminalCapabilities:
    """汇总终端身份、色深和当前主题颜色。"""
    identity: TerminalIdentity
    color_support: TerminalColorSupport
    theme: TerminalTheme = TerminalTheme()

    @property
    def dynamic_surfaces(self) -> bool:
        """判断是否可以安全生成动态 RGB 表面。"""
        return bool(
            self.color_support.effective_level in {
                TerminalColorLevel.TRUECOLOR,
                TerminalColorLevel.ANSI256,
            }
            and self.theme.background is not None
        )

    @property
    def hyperlinks(self) -> bool:
        """判断终端是否可以安全处理 OSC 8 链接。"""
        return self.identity.kind in HYPERLINK_TERMINALS


DEGRADED_TERMINAL_CAPABILITIES = TerminalCapabilities(
    identity=TerminalIdentity(TerminalKind.UNKNOWN, "unknown"),
    color_support=DEGRADED_COLOR_SUPPORT,
)

ColorProbe: typing.TypeAlias = typing.Callable[
    [object, object, float],
    TerminalTheme,
]

class TerminalThemeCache(object):
    """缓存一次 TUI 启动期间的终端主题探测结果。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempted = False
        self._theme = TerminalTheme()

    def get_or_probe(
        self,
        input_stream: object,
        output_stream: object,
        timeout: float,
        probe: ColorProbe,
    ) -> TerminalTheme:
        """首次调用执行探测，后续调用复用同一结果。"""
        with self._lock:
            if self._attempted:
                return self._theme
            self._attempted = True
            try:
                result = probe(input_stream, output_stream, timeout)
                self._theme = (
                    result if isinstance(result, TerminalTheme) else TerminalTheme()
                )
            except (AttributeError, OSError, TypeError, ValueError, RuntimeError):
                self._theme = TerminalTheme()
            return self._theme


def detect_terminal_capabilities(
    *,
    input_stream: object | None = None,
    output_stream: object | None = None,
    environ: typing.Mapping[str, str] | None = None,
    color_probe: ColorProbe | None = None,
    tmux_probe: TmuxProbe | None = None,
    theme_cache: TerminalThemeCache | None = None,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalCapabilities:
    """探测交互终端身份、色深和默认主题颜色。"""
    env = os.environ if environ is None else environ
    identity = detect_terminal_identity(env, tmux_probe=tmux_probe)
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output_stream is None else output_stream
    color_support = detect_terminal_color_support(
        env,
        identity=identity,
        output_stream=stdout,
    )

    if not (
        stream_is_tty(stdin)
        and stream_is_tty(stdout)
    ):
        return TerminalCapabilities(
            identity=identity,
            color_support=color_support,
        )

    probe = color_probe
    if probe is None and input_replay is not None:
        def probe_with_replay(
            input_value: object,
            output_value: object,
            timeout_value: float,
        ) -> TerminalTheme:
            """为默认 Unix 探测附加输入回放回调。"""
            return query_terminal_theme(
                input_value,
                output_value,
                timeout_value,
                input_replay=input_replay,
            )

        probe = probe_with_replay
    probe = probe or query_terminal_theme

    theme = (theme_cache or TerminalThemeCache()).get_or_probe(
        stdin,
        stdout,
        TERMINAL_QUERY_TIMEOUT_SEC,
        probe,
    )
    return TerminalCapabilities(
        identity=identity,
        color_support=color_support,
        theme=theme,
    )


def query_terminal_theme(
    input_stream: object,
    output_stream: object,
    timeout: float = TERMINAL_QUERY_TIMEOUT_SEC,
    *,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalTheme:
    """在短超时内查询终端默认前景色和背景色。"""
    if sys.platform == "win32":
        return _query_windows_theme(input_stream, output_stream, timeout)
    return _query_unix_theme(input_stream, output_stream, timeout, input_replay)


def parse_terminal_color_responses(data: bytes | str) -> TerminalTheme:
    """解析 OSC 10 和 OSC 11 返回的 RGB 颜色。"""
    raw = data.encode("ascii", errors="ignore") if isinstance(data, str) else data

    colors: dict[int, RgbColor] = {}

    for match in re.finditer(
        rb"\x1b](10|11);([^\x07\x1b]*)(?:\x07|\x1b\\)",
        raw,
    ):
        color = _parse_osc_color(match.group(2).decode("ascii", errors="ignore"))
        if color is not None:
            colors[int(match.group(1))] = color

    return TerminalTheme(
        foreground=colors.get(10),
        background=colors.get(11),
    )


def _parse_osc_color(value: str) -> RgbColor | None:
    """解析单项 OSC 颜色值。"""
    text = value.strip()
    if text.startswith("#") and len(text) == 7:
        try:
            return (
                int(text[1:3], 16),
                int(text[3:5], 16),
                int(text[5:7], 16),
            )
        except ValueError:
            return None

    lowered = text.casefold()
    if lowered.startswith(("rgb:", "rgba:")):
        rgba = lowered.startswith("rgba:")
        parts = text[5 if rgba else 4:].split("/")
        if len(parts) != (4 if rgba else 3):
            return None
        try:
            rgb = (
                _scale_hex_component(parts[0]),
                _scale_hex_component(parts[1]),
                _scale_hex_component(parts[2]),
            )
            if rgba:
                _scale_hex_component(parts[3])
            return rgb
        except ValueError:
            return None

    return None


def _scale_hex_component(value: str) -> int:
    """把可变精度十六进制分量缩放到八位颜色。"""
    if len(value) not in {2, 4}:
        raise ValueError("invalid RGB component")
    maximum = (16 ** len(value)) - 1
    return round(int(value, 16) * 255 / maximum)


def _query_unix_theme(
    input_stream: object,
    output_stream: object,
    timeout: float,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalTheme:
    """通过 Unix TTY 查询默认颜色。"""
    import termios

    read_fd: int | None = None
    write_fd: int | None = None
    tty_fd: int | None = None

    original: list[typing.Any] | None = None

    try:
        input_fd = int(input_stream.fileno())  # type: ignore[attr-defined]
        output_fd = int(output_stream.fileno())  # type: ignore[attr-defined]

        if os.isatty(input_fd) and os.isatty(output_fd):
            read_fd = os.dup(input_fd)
            write_fd = os.dup(output_fd)
        else:
            tty_fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            read_fd = os.dup(tty_fd)
            write_fd = os.dup(tty_fd)

        original = termios.tcgetattr(read_fd)

        raw = list(original)
        raw[3] &= ~(termios.ICANON | termios.ECHO)
        raw[6] = list(raw[6])
        raw[6][termios.VMIN] = 0
        raw[6][termios.VTIME] = 0

        termios.tcsetattr(read_fd, termios.TCSANOW, raw)

        os.set_blocking(read_fd, False)
        os.write(write_fd, _terminal_color_query())

        return _read_unix_color_response(read_fd, timeout, input_replay)

    finally:
        if read_fd is not None and original is not None:
            try:
                termios.tcsetattr(read_fd, termios.TCSANOW, original)
            except OSError:
                pass

        for descriptor in (read_fd, write_fd, tty_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _read_unix_color_response(
    read_fd: int,
    timeout: float,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalTheme:
    """在单个截止时间内收集 Unix TTY 颜色响应。"""
    deadline = time.monotonic() + max(0.0, timeout)

    chunks: list[bytes] = []

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())

        readable, _, _ = select.select([read_fd], [], [], remaining)

        if not readable:
            break
        try:
            chunk = os.read(read_fd, 1024)
        except BlockingIOError:
            continue

        if not chunk:
            break

        chunks.append(chunk)

        theme = parse_terminal_color_responses(b"".join(chunks))
        if theme.foreground is not None and theme.background is not None:
            _replay_non_color_input(b"".join(chunks), input_replay)
            return theme

    data = b"".join(chunks)
    _replay_non_color_input(data, input_replay)
    theme = parse_terminal_color_responses(data)
    return theme if (
        theme.foreground is not None
        and theme.background is not None
    ) else TerminalTheme()


def _replay_non_color_input(
    data: bytes,
    input_replay: typing.Callable[[bytes], None] | None,
) -> None:
    """把探测期间夹带的普通输入交还给上层输入实现。"""
    if input_replay is None:
        return
    spans = [match.span() for match in re.finditer(
        rb"\x1b](?:10|11);[^\x07\x1b]*(?:\x07|\x1b\\)",
        data,
    )]
    if not spans:
        if data:
            input_replay(data)
        return
    cursor = 0
    for start, end in spans:
        if start > cursor:
            input_replay(data[cursor:start])
        cursor = end
    if cursor < len(data):
        input_replay(data[cursor:])


def _query_windows_theme(
    input_stream: object,
    output_stream: object,
    timeout: float
) -> TerminalTheme:
    """通过 Windows 控制台 API 查询颜色，不读取 stdin 的 OSC 回复。"""
    del input_stream, timeout
    return _windows_palette_theme(output_stream)


def _windows_handle(stream: object) -> int | None:
    """读取 Python 流对应的 Windows 原生句柄。"""
    import msvcrt

    try:
        handle = int(msvcrt.get_osfhandle(int(stream.fileno())))  # type: ignore[attr-defined]
    except (OSError, ValueError, AttributeError):
        return None

    return handle if handle != -1 else None


def _windows_palette_theme(output_stream: object) -> TerminalTheme:
    """从 Windows 控制台调色板读取当前默认颜色。"""
    import ctypes
    from ctypes import wintypes

    class Coord(ctypes.Structure):
        _fields_ = [("x", wintypes.SHORT), ("y", wintypes.SHORT)]

    class SmallRect(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.SHORT),
            ("top", wintypes.SHORT),
            ("right", wintypes.SHORT),
            ("bottom", wintypes.SHORT),
        ]

    class ScreenBufferInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.ULONG),
            ("dwSize", Coord),
            ("dwCursorPosition", Coord),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SmallRect),
            ("dwMaximumWindowSize", Coord),
            ("wPopupAttributes", wintypes.WORD),
            ("bFullscreenSupported", wintypes.BOOL),
            ("ColorTable", wintypes.DWORD * 16),
        ]

    handle = _windows_handle(output_stream)
    if handle is None:
        return TerminalTheme()

    info = ScreenBufferInfo()
    info.cbSize = ctypes.sizeof(info)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    get_screen_buffer_info = kernel32["GetConsoleScreenBufferInfoEx"]

    get_screen_buffer_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ScreenBufferInfo),
    ]
    get_screen_buffer_info.restype = wintypes.BOOL

    if not get_screen_buffer_info(
        wintypes.HANDLE(handle),
        ctypes.pointer(info),
    ):
        return TerminalTheme()

    foreground_index = info.wAttributes & 0x0F
    background_index = (info.wAttributes >> 4) & 0x0F

    return TerminalTheme(
        foreground=_colorref_rgb(info.ColorTable[foreground_index]),
        background=_colorref_rgb(info.ColorTable[background_index]),
    )


def _colorref_rgb(value: int) -> RgbColor:
    """把 Windows COLORREF 转换为 RGB 元组。"""
    return value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF


def _terminal_color_query() -> bytes:
    """生成默认前景色和背景色的 OSC 查询。"""
    return b"\x1b]10;?\x1b\\\x1b]11;?\x1b\\"


if __name__ == '__main__':
    pass
