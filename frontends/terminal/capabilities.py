# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import typing
from dataclasses import dataclass

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
    """汇总当前 TUI 会话的终端身份、色深和主题快照。"""

    identity: TerminalIdentity
    color_support: TerminalColorSupport
    theme: TerminalTheme = TerminalTheme()


DEGRADED_TERMINAL_CAPABILITIES = TerminalCapabilities(
    identity=TerminalIdentity(TerminalKind.UNKNOWN, "unknown"),
    color_support=DEGRADED_COLOR_SUPPORT,
)


def detect_terminal_capabilities(
    *,
    input_stream: object | None = None,
    output_stream: object | None = None,
    environ: typing.Mapping[str, str] | None = None,
    color_probe: DefaultColorsProbe | None = None,
    tmux_probe: TmuxProbe | None = None,
    default_colors_cache: TerminalDefaultColorsCache | None = None,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalCapabilities:
    """为当前 TUI 会话探测一次终端身份、色深和默认颜色。"""

    env = os.environ if environ is None else environ
    identity = detect_terminal_identity(env, tmux_probe=tmux_probe)
    stdin = sys.stdin if input_stream is None else input_stream
    stdout = sys.stdout if output_stream is None else output_stream
    color_support = detect_terminal_color_support(
        env,
        identity=identity,
        output_stream=stdout,
    )

    if not (stream_is_tty(stdin) and stream_is_tty(stdout)):
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
    )


if __name__ == '__main__':
    pass
