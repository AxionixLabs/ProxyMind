# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import sys
import time
import typing
import select
import subprocess
import threading
from dataclasses import dataclass
from enum import Enum

RgbColor: typing.TypeAlias = tuple[int, int, int]

TERMINAL_QUERY_TIMEOUT_SEC = 0.1


class TerminalKind(str, Enum):
    """描述当前交互终端的已知身份。"""
    WINDOWS_TERMINAL = "windows_terminal"
    ITERM2           = "iterm2"
    WEZTERM          = "wezterm"
    GHOSTTY          = "ghostty"
    KITTY            = "kitty"
    ALACRITTY        = "alacritty"
    KONSOLE          = "konsole"
    FOOT             = "foot"
    RIO              = "rio"
    WARP             = "warp"
    APPLE_TERMINAL   = "apple_terminal"
    GNOME_TERMINAL   = "gnome_terminal"
    VSCODE           = "vscode"
    VTE              = "vte"
    TMUX             = "tmux"
    ZELLIJ           = "zellij"
    DUMB             = "dumb"
    UNKNOWN          = "unknown"


class TerminalColorLevel(str, Enum):
    """描述标准输出可安全使用的颜色级别。"""
    TRUECOLOR = "truecolor"
    ANSI256   = "ansi256"
    ANSI16    = "ansi16"
    UNKNOWN   = "unknown"


HIGH_CAPABILITY_TERMINALS = frozenset({
    TerminalKind.WINDOWS_TERMINAL,
    TerminalKind.ITERM2,
    TerminalKind.WEZTERM,
    TerminalKind.GHOSTTY,
    TerminalKind.KITTY,
    TerminalKind.ALACRITTY,
    TerminalKind.KONSOLE,
    TerminalKind.APPLE_TERMINAL,
})

DYNAMIC_SURFACE_TERMINALS = HIGH_CAPABILITY_TERMINALS - {TerminalKind.APPLE_TERMINAL}

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
class TerminalIdentity:
    """保存终端身份及其外层复用器信息。"""
    kind: TerminalKind
    name: str
    multiplexer: TerminalKind | None = None
    term_program: str | None = None
    version: str | None = None
    term: str | None = None
    multiplexer_version: str | None = None

    @property
    def high_capability(self) -> bool:
        """判断终端是否位于高能力白名单。"""
        return self.kind in HIGH_CAPABILITY_TERMINALS

    @property
    def supports_dynamic_surfaces(self) -> bool:
        """判断终端是否适合展示动态背景表面。"""
        return self.kind in DYNAMIC_SURFACE_TERMINALS


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
    color_level: TerminalColorLevel
    theme: TerminalTheme = TerminalTheme()

    @property
    def dynamic_surfaces(self) -> bool:
        """判断是否可以安全生成动态 RGB 表面。"""
        return bool(
            self.color_level in {
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
    color_level=TerminalColorLevel.UNKNOWN,
)


ColorProbe: typing.TypeAlias = typing.Callable[
    [object, object, float],
    TerminalTheme,
]
TmuxProbe: typing.TypeAlias = typing.Callable[[], tuple[str, str] | None]


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
    env      = os.environ if environ is None else environ
    identity = detect_terminal_identity(env, tmux_probe=tmux_probe)
    stdin    = sys.stdin if input_stream is None else input_stream
    stdout   = sys.stdout if output_stream is None else output_stream
    level    = detect_terminal_color_level(
        env,
        identity=identity,
        output_stream=stdout,
    )

    if not (
        _stream_is_tty(stdin)
        and _stream_is_tty(stdout)
    ):
        return TerminalCapabilities(identity=identity, color_level=level)

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
        color_level=level,
        theme=theme,
    )


def detect_terminal_identity(
    environ: typing.Mapping[str, str] | None = None,
    *,
    tmux_probe: TmuxProbe | None = None
) -> TerminalIdentity:
    """按既定优先级识别终端、版本和外层复用器。"""
    env = os.environ if environ is None else environ
    multiplexer, mux_version = _detect_multiplexer(env)
    program = str(env.get("TERM_PROGRAM") or "").strip()

    if program:
        if multiplexer == TerminalKind.TMUX:
            queried = (tmux_probe or _query_tmux_client)()
            if queried is not None:
                term_type, term_name = queried
                kind, version = _kind_and_version_from_program(term_type)
                return TerminalIdentity(
                    kind,
                    term_name or term_type or "tmux",
                    multiplexer=multiplexer,
                    term_program=term_type or None,
                    version=version,
                    term=term_name or None,
                    multiplexer_version=mux_version,
                )

        kind, version = _kind_and_version_from_program(program)
        version = version or str(env.get("TERM_PROGRAM_VERSION") or "").strip() or None
        return TerminalIdentity(
            kind,
            program,
            multiplexer=multiplexer,
            term_program=program,
            version=version,
            term=str(env.get("TERM") or "").strip() or None,
            multiplexer_version=mux_version,
        )

    direct_signals = (
        ("WEZTERM_VERSION", TerminalKind.WEZTERM, "WezTerm"),
        ("ITERM_SESSION_ID", TerminalKind.ITERM2, "iTerm2"),
        ("ITERM_PROFILE", TerminalKind.ITERM2, "iTerm2"),
        ("ITERM_PROFILE_NAME", TerminalKind.ITERM2, "iTerm2"),
        ("TERM_SESSION_ID", TerminalKind.APPLE_TERMINAL, "Apple Terminal"),
        ("KITTY_WINDOW_ID", TerminalKind.KITTY, "Kitty"),
        ("ALACRITTY_SOCKET", TerminalKind.ALACRITTY, "Alacritty"),
        ("KONSOLE_VERSION", TerminalKind.KONSOLE, "Konsole"),
        ("GNOME_TERMINAL_SCREEN", TerminalKind.GNOME_TERMINAL, "GNOME Terminal"),
        ("VTE_VERSION", TerminalKind.VTE, "VTE"),
        ("WT_SESSION", TerminalKind.WINDOWS_TERMINAL, "Windows Terminal"),
    )
    for variable, kind, name in direct_signals:
        value = str(env.get(variable) or "").strip()
        if value:
            return TerminalIdentity(
                kind,
                name,
                multiplexer=multiplexer,
                version=value if variable.endswith("VERSION") else None,
                term=str(env.get("TERM") or "").strip() or None,
                multiplexer_version=mux_version,
            )

    if multiplexer == TerminalKind.TMUX:
        queried = (tmux_probe or _query_tmux_client)()
        if queried is not None:
            term_type, term_name = queried
            kind, version = _kind_and_version_from_program(term_type)
            return TerminalIdentity(
                kind,
                term_name or term_type or "tmux",
                multiplexer=multiplexer,
                term_program=term_type or None,
                version=version,
                term=term_name or None,
                multiplexer_version=mux_version,
            )
        return TerminalIdentity(
            TerminalKind.TMUX,
            "tmux",
            multiplexer=multiplexer,
            multiplexer_version=mux_version,
        )

    term = str(env.get("TERM") or "").strip()
    kind = _kind_from_term(term)
    return TerminalIdentity(
        kind,
        term or "unknown",
        multiplexer=multiplexer,
        term=term or None,
        multiplexer_version=mux_version,
    )


def detect_terminal_color_level(
    environ: typing.Mapping[str, str] | None = None,
    *,
    identity: TerminalIdentity | None = None,
    output_stream: object | None = None,
) -> TerminalColorLevel:
    """按 supports-color 语义判断标准输出可用色深。"""
    env = os.environ if environ is None else environ

    if "FORCE_COLOR" in env:
        return _forced_color_level(str(env.get("FORCE_COLOR") or ""))
    if "NO_COLOR" in env:
        return TerminalColorLevel.UNKNOWN

    color_term = str(env.get("COLORTERM") or "").casefold()
    if color_term in {"truecolor", "24bit"}:
        level = TerminalColorLevel.TRUECOLOR
    else:
        term = str(env.get("TERM") or "").casefold()
        if term == "dumb":
            return TerminalColorLevel.UNKNOWN
        if any(token in term for token in ("truecolor", "24bit", "direct")):
            level = TerminalColorLevel.TRUECOLOR
        elif "256color" in term:
            level = TerminalColorLevel.ANSI256
        elif not term:
            level = TerminalColorLevel.UNKNOWN
        elif output_stream is None or _stream_is_tty(output_stream):
            level = TerminalColorLevel.ANSI16
        else:
            level = TerminalColorLevel.UNKNOWN

    if str(env.get("WT_SESSION") or "").strip():
        # Windows Terminal advertises ANSI16 while still supporting RGB.
        level = TerminalColorLevel.TRUECOLOR
    elif (
        "FORCE_COLOR" not in env
        and (identity or detect_terminal_identity(env)).kind is TerminalKind.WINDOWS_TERMINAL
        and level is TerminalColorLevel.ANSI16
    ):
        level = TerminalColorLevel.TRUECOLOR

    if output_stream is not None and not _stream_is_tty(output_stream):
        return TerminalColorLevel.UNKNOWN
    return level


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


def _detect_multiplexer(
    env: typing.Mapping[str, str],
) -> tuple[TerminalKind | None, str | None]:
    """按环境信号识别外层复用器。"""
    if (
        str(env.get("ZELLIJ") or "").strip()
        or str(env.get("ZELLIJ_SESSION_NAME") or "").strip()
        or str(env.get("ZELLIJ_VERSION") or "").strip()
    ):
        return TerminalKind.ZELLIJ, str(env.get("ZELLIJ_VERSION") or "").strip() or None
    if str(env.get("TMUX") or "").strip() or str(env.get("TMUX_PANE") or "").strip():
        return TerminalKind.TMUX, _tmux_version(env)
    return None, None


def _tmux_version(env: typing.Mapping[str, str]) -> str | None:
    """读取可选的 tmux 版本环境值。"""
    value = str(env.get("TMUX_VERSION") or "").strip()
    return value or None


def _kind_and_version_from_program(value: str) -> tuple[TerminalKind, str | None]:
    """将 TERM_PROGRAM 或 tmux termtype 精确映射为终端身份。"""
    raw = str(value or "").strip()
    program_token, _, suffix = raw.partition(" ")
    normalized = re.sub(r"[ ._\-]", "", raw).casefold()
    normalized_token = re.sub(r"[ ._\-]", "", program_token).casefold()
    known = {
        "appleterminal": TerminalKind.APPLE_TERMINAL,
        "ghostty": TerminalKind.GHOSTTY,
        "itermapp": TerminalKind.ITERM2,
        "iterm2": TerminalKind.ITERM2,
        "kitty": TerminalKind.KITTY,
        "alacritty": TerminalKind.ALACRITTY,
        "konsole": TerminalKind.KONSOLE,
        "gnometerminal": TerminalKind.GNOME_TERMINAL,
        "vscode": TerminalKind.VSCODE,
        "vscodeinsiders": TerminalKind.VSCODE,
        "wezterm": TerminalKind.WEZTERM,
        "windowsterminal": TerminalKind.WINDOWS_TERMINAL,
    }
    match = known.get(normalized, known.get(normalized_token, TerminalKind.UNKNOWN))
    if match == TerminalKind.UNKNOWN:
        match = _kind_from_term(raw)

    version = suffix.strip(" /-") or None
    if version is None and raw.casefold().startswith("wezterm"):
        version = raw[len("wezterm"):].strip(" /-") or None
    return match, version


def _kind_from_term(value: str) -> TerminalKind:
    """将 TERM 值按保守规则映射为终端身份。"""
    text = str(value or "").strip().casefold()
    if text == "dumb":
        return TerminalKind.DUMB
    if "kitty" in text:
        return TerminalKind.KITTY
    if text == "alacritty":
        return TerminalKind.ALACRITTY
    if "ghostty" in text:
        return TerminalKind.GHOSTTY
    if "wezterm" in text:
        return TerminalKind.WEZTERM
    if "konsole" in text:
        return TerminalKind.KONSOLE
    if "vte" in text:
        return TerminalKind.VTE
    if "tmux" in text:
        return TerminalKind.TMUX
    if "zellij" in text:
        return TerminalKind.ZELLIJ
    return TerminalKind.UNKNOWN


def _forced_color_level(value: str) -> TerminalColorLevel:
    """把 FORCE_COLOR 值转换为明确色深。"""
    normalized = value.strip().casefold()

    if normalized == "0":
        return TerminalColorLevel.UNKNOWN
    if normalized in {"3", "truecolor", "24bit"}:
        return TerminalColorLevel.TRUECOLOR
    if normalized == "2":
        return TerminalColorLevel.ANSI256

    return TerminalColorLevel.ANSI16


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


def _stream_is_tty(stream: object) -> bool:
    """判断流是否连接到交互终端。"""
    isatty = getattr(stream, "isatty", None)

    try:
        return bool(callable(isatty) and isatty())
    except (OSError, ValueError):
        return False


def _query_tmux_client() -> tuple[str, str] | None:
    """读取 tmux 客户端的 termtype 和 TERM。"""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-p",
                "#{client_termtype}\t#{client_termname}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=TERMINAL_QUERY_TIMEOUT_SEC,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    term_type, _, name = result.stdout.strip().partition("\t")

    return (term_type.strip(), name.strip()) if name or term_type else None


def _query_unix_theme(
    input_stream: object,
    output_stream: object,
    timeout: float,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalTheme:
    """通过 Unix TTY 查询默认颜色。"""
    import termios

    read_fd: int | None  = None
    write_fd: int | None = None
    tty_fd: int | None   = None

    original: list[typing.Any] | None = None

    try:
        input_fd  = int(input_stream.fileno())   # type: ignore[attr-defined]
        output_fd = int(output_stream.fileno())  # type: ignore[attr-defined]

        if os.isatty(input_fd) and os.isatty(output_fd):
            read_fd  = os.dup(input_fd)
            write_fd = os.dup(output_fd)
        else:
            tty_fd   = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            read_fd  = os.dup(tty_fd)
            write_fd = os.dup(tty_fd)

        original = termios.tcgetattr(read_fd)

        raw = list(original)
        raw[3] &= ~(termios.ICANON | termios.ECHO)
        raw[6] = list(raw[6])
        raw[6][termios.VMIN]  = 0
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

    info        = ScreenBufferInfo()
    info.cbSize = ctypes.sizeof(info)
    kernel32    = ctypes.WinDLL("kernel32", use_last_error=True)

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
