# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import sys
import time
import typing
import select
import subprocess
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
    VSCODE           = "vscode"
    VTE              = "vte"
    TMUX             = "tmux"
    ZELLIJ           = "zellij"
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
    TerminalKind.FOOT,
    TerminalKind.RIO,
    TerminalKind.WARP,
    TerminalKind.APPLE_TERMINAL,
})

HYPERLINK_TERMINALS = frozenset({
    *(HIGH_CAPABILITY_TERMINALS - {TerminalKind.APPLE_TERMINAL}),
    TerminalKind.VSCODE,
    TerminalKind.VTE,
    TerminalKind.ZELLIJ,
})


@dataclass(frozen=True)
class TerminalIdentity:
    """保存终端身份及其外层复用器信息。"""
    kind: TerminalKind
    name: str
    multiplexer: TerminalKind | None = None

    @property
    def high_capability(self) -> bool:
        """判断终端是否位于高能力白名单。"""
        return self.kind in HIGH_CAPABILITY_TERMINALS


@dataclass(frozen=True)
class TerminalTheme:
    """保存终端报告的默认前景色和背景色。"""
    foreground: RgbColor | None = None
    background: RgbColor | None = None


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
            self.identity.high_capability
            and self.color_level == TerminalColorLevel.TRUECOLOR
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


def detect_terminal_capabilities(
    *,
    input_stream: object | None = None,
    output_stream: object | None = None,
    environ: typing.Mapping[str, str] | None = None,
    color_probe: ColorProbe | None = None,
    tmux_probe: TmuxProbe | None = None
) -> TerminalCapabilities:
    """探测交互终端身份、色深和默认主题颜色。"""
    env      = os.environ if environ is None else environ
    identity = detect_terminal_identity(env, tmux_probe=tmux_probe)
    level    = detect_terminal_color_level(env, identity=identity)
    stdin    = sys.stdin if input_stream is None else input_stream
    stdout   = sys.stdout if output_stream is None else output_stream

    if not (
        identity.high_capability
        and level == TerminalColorLevel.TRUECOLOR
        and _stream_is_tty(stdin)
        and _stream_is_tty(stdout)
    ):
        return TerminalCapabilities(identity=identity, color_level=level)

    probe = color_probe or query_terminal_theme

    try:
        theme = probe(stdin, stdout, TERMINAL_QUERY_TIMEOUT_SEC)
    except (AttributeError, OSError, TypeError, ValueError, RuntimeError):
        theme = TerminalTheme()
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
    """按稳定环境信号识别当前终端及复用器外层终端。"""
    env     = os.environ if environ is None else environ
    program = str(env.get("TERM_PROGRAM") or "").strip()

    if program:
        return TerminalIdentity(_kind_from_text(program), program)

    direct_signals = (
        ("WEZTERM_VERSION", TerminalKind.WEZTERM, "WezTerm"),
        ("ITERM_SESSION_ID", TerminalKind.ITERM2, "iTerm2"),
        ("ITERM_PROFILE", TerminalKind.ITERM2, "iTerm2"),
        ("KITTY_WINDOW_ID", TerminalKind.KITTY, "Kitty"),
        ("ALACRITTY_SOCKET", TerminalKind.ALACRITTY, "Alacritty"),
        ("KONSOLE_VERSION", TerminalKind.KONSOLE, "Konsole"),
        ("GNOME_TERMINAL_SCREEN", TerminalKind.VTE, "GNOME Terminal"),
        ("VTE_VERSION", TerminalKind.VTE, "VTE"),
        ("WT_SESSION", TerminalKind.WINDOWS_TERMINAL, "Windows Terminal"),
    )
    for variable, kind, name in direct_signals:
        if str(env.get(variable) or "").strip():
            return TerminalIdentity(kind, name)

    lc_terminal = str(env.get("LC_TERMINAL") or "").strip()
    if lc_terminal:
        return TerminalIdentity(_kind_from_text(lc_terminal), lc_terminal)

    if str(env.get("ZELLIJ") or "").strip():
        return TerminalIdentity(
            TerminalKind.ZELLIJ,
            "Zellij",
            multiplexer=TerminalKind.ZELLIJ,
        )

    if str(env.get("TMUX") or "").strip():
        terms = (tmux_probe or _query_tmux_client)()
        if terms is not None:
            client_name, client_type = terms

            kind = _kind_from_text(f"{client_name} {client_type}")

            return TerminalIdentity(
                kind,
                client_name or client_type or "tmux",
                multiplexer=TerminalKind.TMUX,
            )

        return TerminalIdentity(
            TerminalKind.TMUX,
            "tmux",
            multiplexer=TerminalKind.TMUX,
        )

    session_id = str(env.get("TERM_SESSION_ID") or "").strip()
    if session_id:
        return TerminalIdentity(TerminalKind.APPLE_TERMINAL, "Apple Terminal")

    term = str(env.get("TERM") or "").strip()

    return TerminalIdentity(_kind_from_text(term), term or "unknown")


def detect_terminal_color_level(
    environ: typing.Mapping[str, str] | None = None,
    *,
    identity: TerminalIdentity | None = None
) -> TerminalColorLevel:
    """根据显式覆盖、环境能力和终端身份判断标准输出色深。"""
    env = os.environ if environ is None else environ

    if "FORCE_COLOR" in env:
        return _forced_color_level(str(env.get("FORCE_COLOR") or ""))
    if "NO_COLOR" in env:
        return TerminalColorLevel.UNKNOWN

    requested_depth = str(env.get("PROMPT_TOOLKIT_COLOR_DEPTH") or "").upper()
    if requested_depth in {"DEPTH_24_BIT", "TRUECOLOR", "24BIT"}:
        return TerminalColorLevel.TRUECOLOR
    if requested_depth == "DEPTH_8_BIT":
        return TerminalColorLevel.ANSI256
    if requested_depth == "DEPTH_4_BIT":
        return TerminalColorLevel.ANSI16

    if str(env.get("WT_SESSION") or "").strip():
        return TerminalColorLevel.TRUECOLOR

    color_term = str(env.get("COLORTERM") or "").casefold()
    if color_term in {"truecolor", "24bit"}:
        return TerminalColorLevel.TRUECOLOR

    resolved_identity = identity or detect_terminal_identity(env)
    if resolved_identity.high_capability:
        return TerminalColorLevel.TRUECOLOR

    term = str(env.get("TERM") or "").casefold()
    if term == "dumb" or not term:
        return TerminalColorLevel.UNKNOWN
    if any(token in term for token in ("truecolor", "24bit", "direct")):
        return TerminalColorLevel.TRUECOLOR
    if "256color" in term:
        return TerminalColorLevel.ANSI256

    return TerminalColorLevel.ANSI16


def query_terminal_theme(
    input_stream: object,
    output_stream: object,
    timeout: float = TERMINAL_QUERY_TIMEOUT_SEC
) -> TerminalTheme:
    """在短超时内查询终端默认前景色和背景色。"""
    if sys.platform == "win32":
        return _query_windows_theme(input_stream, output_stream, timeout)
    return _query_unix_theme(input_stream, output_stream, timeout)


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


def _kind_from_text(value: str) -> TerminalKind:
    """把终端名称或 TERM 能力字符串归一化为已知身份。"""
    text = str(value or "").casefold()

    checks = (
        (("windows terminal", "windows_terminal"), TerminalKind.WINDOWS_TERMINAL),
        (("iterm",), TerminalKind.ITERM2),
        (("wezterm",), TerminalKind.WEZTERM),
        (("ghostty",), TerminalKind.GHOSTTY),
        (("kitty",), TerminalKind.KITTY),
        (("alacritty",), TerminalKind.ALACRITTY),
        (("konsole",), TerminalKind.KONSOLE),
        (("foot",), TerminalKind.FOOT),
        (("rio",), TerminalKind.RIO),
        (("warp",), TerminalKind.WARP),
        (("apple_terminal", "apple terminal"), TerminalKind.APPLE_TERMINAL),
        (("vscode",), TerminalKind.VSCODE),
        (("gnome", "vte"), TerminalKind.VTE),
        (("tmux",), TerminalKind.TMUX),
        (("zellij",), TerminalKind.ZELLIJ),
    )

    for aliases, kind in checks:
        if any(alias in text for alias in aliases):
            return kind

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

    if text.casefold().startswith("rgb:"):
        parts = text[4:].split("/")
        if len(parts) != 3:
            return None
        try:
            return (
                _scale_hex_component(parts[0]),
                _scale_hex_component(parts[1]),
                _scale_hex_component(parts[2]),
            )
        except ValueError:
            return None

    return None


def _scale_hex_component(value: str) -> int:
    """把可变精度十六进制分量缩放到八位颜色。"""
    if not 1 <= len(value) <= 4:
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
    """读取 tmux 客户端实际使用的终端名称和类型。"""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-p",
                "#{client_termname}\t#{client_termtype}",
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

    name, _, term_type = result.stdout.strip().partition("\t")

    return (name.strip(), term_type.strip()) if name or term_type else None


def _query_unix_theme(
    input_stream: object,
    output_stream: object,
    timeout: float
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

        return _read_unix_color_response(read_fd, timeout)

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


def _read_unix_color_response(read_fd: int, timeout: float) -> TerminalTheme:
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
            return theme

    return parse_terminal_color_responses(b"".join(chunks))


def _query_windows_theme(
    input_stream: object,
    output_stream: object,
    timeout: float
) -> TerminalTheme:
    """通过 Windows 控制台查询默认颜色并提供调色板回退。"""
    import ctypes
    from ctypes import wintypes

    input_handle = _windows_handle(input_stream)

    console_handle = (
        wintypes.HANDLE(input_handle)
        if input_handle is not None
        else None
    )

    original_mode = wintypes.DWORD()
    kernel32      = ctypes.WinDLL("kernel32", use_last_error=True)

    get_console_mode = kernel32["GetConsoleMode"]

    get_console_mode.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]

    get_console_mode.restype = wintypes.BOOL

    set_console_mode          = kernel32["SetConsoleMode"]
    set_console_mode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    set_console_mode.restype  = wintypes.BOOL

    mode_changed: bool = False

    if console_handle is not None and get_console_mode(
        console_handle,
        ctypes.pointer(original_mode),
    ):
        mode_changed = bool(set_console_mode(
            console_handle,
            original_mode.value | 0x0200,
        ))

    try:
        _write_terminal_query(output_stream)
        osc_theme = _read_windows_color_response(timeout)

    finally:
        if console_handle is not None and mode_changed:
            set_console_mode(
                console_handle,
                original_mode.value,
            )

    palette_theme = _windows_palette_theme(output_stream)

    return TerminalTheme(
        foreground=osc_theme.foreground or palette_theme.foreground,
        background=osc_theme.background or palette_theme.background,
    )


def _read_windows_color_response(timeout: float) -> TerminalTheme:
    """在截止时间内收集 Windows 控制台颜色响应。"""
    import msvcrt

    deadline = time.monotonic() + max(0.0, timeout)
    response: list[str] = []

    while True:
        while msvcrt.kbhit():
            response.append(msvcrt.getwch())

        theme = parse_terminal_color_responses("".join(response))
        if theme.foreground is not None and theme.background is not None:
            return theme

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return theme
        time.sleep(min(0.002, remaining))


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


def _write_terminal_query(output_stream: object) -> None:
    """向文本或二进制输出流写入颜色查询。"""
    query  = _terminal_color_query()
    writer = getattr(output_stream, "buffer", output_stream)

    try:
        writer.write(query)
    except TypeError:
        writer.write(query.decode("ascii"))

    writer.flush()


if __name__ == '__main__':
    pass
