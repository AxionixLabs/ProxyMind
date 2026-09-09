# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import typing
from dataclasses import dataclass
from enum import Enum

from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.output.win32 import (
    NoConsoleScreenBufferError,
    Win32Output,
)

from .color_support import (
    DEGRADED_COLOR_SUPPORT,
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
from .probe import (
    TERMINAL_QUERY_TIMEOUT_SEC,
    DefaultColorsProbe,
    RgbColor,
    TerminalDefaultColors,
    TerminalDefaultColorsCache,
    TerminalProbeMethod,
    query_terminal_default_colors,
)


class TerminalCapabilityState(str, Enum):
    """表示一次终端能力探测的支持、拒绝或未知结果。"""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TerminalOutputCapabilities:
    """保存当前输出对象可用的物理终端能力快照。"""

    absolute_cursor_addressing: TerminalCapabilityState = (
        TerminalCapabilityState.UNKNOWN
    )
    synchronized_output: TerminalCapabilityState = (
        TerminalCapabilityState.UNKNOWN
    )
    viewport_size: TerminalCapabilityState = TerminalCapabilityState.UNKNOWN
    startup_cursor_position: TerminalCapabilityState = (
        TerminalCapabilityState.UNKNOWN
    )


UNKNOWN_TERMINAL_OUTPUT_CAPABILITIES = TerminalOutputCapabilities()

UNSUPPORTED_TERMINAL_OUTPUT_CAPABILITIES = TerminalOutputCapabilities(
    absolute_cursor_addressing=TerminalCapabilityState.UNSUPPORTED,
    synchronized_output=TerminalCapabilityState.UNSUPPORTED,
    viewport_size=TerminalCapabilityState.UNSUPPORTED,
    startup_cursor_position=TerminalCapabilityState.UNSUPPORTED,
)


@dataclass(frozen=True)
class TerminalTheme:
    """保存终端报告的默认颜色及其探测事实。"""

    foreground: RgbColor | None = None
    background: RgbColor | None = None
    probe_attempted: bool = False
    probe_method: TerminalProbeMethod = TerminalProbeMethod.NOT_ATTEMPTED

    @classmethod
    def from_default_colors(cls, colors: TerminalDefaultColors) -> "TerminalTheme":
        """把平台探测结果投影为当前渲染快照。"""

        return cls(
            foreground=colors.foreground,
            background=colors.background,
            probe_attempted=colors.attempted,
            probe_method=colors.method,
        )


@dataclass(frozen=True)
class TerminalCapabilities:
    """汇总当前 TUI 会话的身份、色深、主题和输出能力快照。"""

    identity: TerminalIdentity
    color_support: TerminalColorSupport
    theme: TerminalTheme = TerminalTheme()
    output_capabilities: TerminalOutputCapabilities = (
        UNKNOWN_TERMINAL_OUTPUT_CAPABILITIES
    )


DEGRADED_TERMINAL_CAPABILITIES = TerminalCapabilities(
    identity=TerminalIdentity(TerminalKind.UNKNOWN, "unknown"),
    color_support=DEGRADED_COLOR_SUPPORT,
)


_OUTPUT_PROBE_ERRORS = (
    AttributeError,
    NoConsoleScreenBufferError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


def detect_terminal_output_capabilities(
    output: Output | None,
) -> TerminalOutputCapabilities:
    """在 platform adapter 边界探测一次输出对象的物理能力。"""

    if output is None:
        return UNKNOWN_TERMINAL_OUTPUT_CAPABILITIES
    if isinstance(output, (DummyOutput, PlainTextOutput)):
        return UNSUPPORTED_TERMINAL_OUTPUT_CAPABILITIES

    vt100_output = _resolve_vt100_output(output)
    has_win32_cursor_api = _has_win32_cursor_api(output)
    if vt100_output is not None:
        startup_cursor_position = (
            _probe_win32_cursor_position(output)
            if has_win32_cursor_api
            else _probe_boolean(lambda: output.responds_to_cpr)
        )
        return TerminalOutputCapabilities(
            absolute_cursor_addressing=TerminalCapabilityState.SUPPORTED,
            synchronized_output=TerminalCapabilityState.SUPPORTED,
            viewport_size=_probe_viewport_size(output),
            startup_cursor_position=startup_cursor_position,
        )

    if isinstance(output, Win32Output) or has_win32_cursor_api:
        return TerminalOutputCapabilities(
            absolute_cursor_addressing=TerminalCapabilityState.SUPPORTED,
            synchronized_output=TerminalCapabilityState.UNSUPPORTED,
            viewport_size=_probe_viewport_size(output),
            startup_cursor_position=_probe_win32_cursor_position(output),
        )

    return UNKNOWN_TERMINAL_OUTPUT_CAPABILITIES


def _resolve_vt100_output(output: Output) -> Vt100_Output | None:
    """返回输出对象中的 VT 实现，避免仅凭终端身份猜测。"""

    if isinstance(output, Vt100_Output):
        return output
    try:
        nested = getattr(output, "vt100_output")
    except _OUTPUT_PROBE_ERRORS:
        return None
    return nested if isinstance(nested, Vt100_Output) else None


def _has_win32_cursor_api(output: Output) -> bool:
    """判断输出对象是否暴露 Windows Console 光标查询 adapter。"""

    try:
        getter = getattr(output, "get_win32_screen_buffer_info")
    except _OUTPUT_PROBE_ERRORS:
        return False
    return callable(getter)


def _probe_boolean(
    probe: typing.Callable[[], bool],
) -> TerminalCapabilityState:
    """把无副作用的布尔能力查询转换为三态结果。"""

    try:
        return (
            TerminalCapabilityState.SUPPORTED
            if probe()
            else TerminalCapabilityState.UNSUPPORTED
        )
    except _OUTPUT_PROBE_ERRORS:
        return TerminalCapabilityState.UNKNOWN


def _probe_viewport_size(output: Output) -> TerminalCapabilityState:
    """验证输出对象能否读取有效的正尺寸视口。"""

    try:
        size = output.get_size()
        rows = size.rows
        columns = size.columns
    except _OUTPUT_PROBE_ERRORS:
        return TerminalCapabilityState.UNKNOWN
    if (
        not isinstance(rows, int)
        or not isinstance(columns, int)
        or rows <= 0
        or columns <= 0
    ):
        return TerminalCapabilityState.UNKNOWN
    return TerminalCapabilityState.SUPPORTED


def _probe_win32_cursor_position(output: Output) -> TerminalCapabilityState:
    """验证 Windows Console adapter 能否读取启动光标坐标。"""

    try:
        info_getter = getattr(output, "get_win32_screen_buffer_info")
        info = info_getter()
        position = getattr(info, "dwCursorPosition")
        row = getattr(position, "Y")
        column = getattr(position, "X")
    except _OUTPUT_PROBE_ERRORS:
        return TerminalCapabilityState.UNKNOWN
    if not isinstance(row, int) or not isinstance(column, int):
        return TerminalCapabilityState.UNKNOWN
    return TerminalCapabilityState.SUPPORTED


def detect_terminal_capabilities(
    *,
    input_stream: object | None = None,
    output_stream: object | None = None,
    environ: typing.Mapping[str, str] | None = None,
    color_probe: DefaultColorsProbe | None = None,
    tmux_probe: TmuxProbe | None = None,
    default_colors_cache: TerminalDefaultColorsCache | None = None,
    input_replay: typing.Callable[[bytes], None] | None = None,
    output_obj: Output | None = None,
) -> TerminalCapabilities:
    """为当前 TUI 会话探测一次身份、色深、主题和输出能力。"""

    env = os.environ if environ is None else environ
    identity = detect_terminal_identity(env, tmux_probe=tmux_probe)
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output_stream is None else output_stream
    output_capabilities = detect_terminal_output_capabilities(output_obj)
    color_support = detect_terminal_color_support(
        env,
        identity=identity,
        output_stream=stdout,
    )

    if not (stream_is_tty(stdin) and stream_is_tty(stdout)):
        return TerminalCapabilities(
            identity=identity,
            color_support=color_support,
            output_capabilities=output_capabilities,
        )

    probe = color_probe
    if probe is None and input_replay is not None:
        def probe_with_replay(
            input_value: object,
            output_value: object,
            timeout_value: float,
        ) -> TerminalDefaultColors:
            """为默认 Unix 探测附加输入回放回调。"""

            return query_terminal_default_colors(
                input_value,
                output_value,
                timeout_value,
                input_replay=input_replay,
            )

        probe = probe_with_replay
    probe = probe or query_terminal_default_colors

    colors = (default_colors_cache or TerminalDefaultColorsCache()).get_or_probe(
        stdin,
        stdout,
        TERMINAL_QUERY_TIMEOUT_SEC,
        probe,
    )
    return TerminalCapabilities(
        identity=identity,
        color_support=color_support,
        theme=TerminalTheme.from_default_colors(colors),
        output_capabilities=output_capabilities,
    )


if __name__ == '__main__':
    pass
