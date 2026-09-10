# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from dataclasses import dataclass
from enum import Enum

from .identity import (
    TerminalIdentity,
    TerminalKind,
    detect_terminal_identity,
)


class TerminalColorLevel(str, Enum):
    """描述标准输出可安全使用的颜色级别。"""

    TRUECOLOR = "truecolor"
    ANSI256 = "ansi256"
    ANSI16 = "ansi16"
    NONE = "none"
    UNKNOWN = "unknown"


class TerminalColorSource(str, Enum):
    """描述色深结论所使用的信号来源。"""

    FORCE_COLOR = "force_color"
    NO_COLOR = "no_color"
    COLORTERM = "colorterm"
    TERM = "term"
    WINDOWS_TERMINAL = "windows_terminal"
    NON_TTY = "non_tty"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TerminalColorSupport:
    """保存原始色深、有效色深及其判定来源。"""

    raw_level: TerminalColorLevel
    effective_level: TerminalColorLevel
    raw_source: TerminalColorSource
    effective_source: TerminalColorSource
    output_is_tty: bool | None
    explicitly_disabled: bool = False

    @property
    def render_level(self) -> TerminalColorLevel:
        """保留探测事实，并把未知色深降为基础 ANSI 渲染。"""
        return resolve_color_render_level(self.effective_level)

    @classmethod
    def fixed(cls, level: TerminalColorLevel) -> "TerminalColorSupport":
        """为测试或无探测前端构造明确的固定色深。"""

        return cls(
            raw_level=level,
            effective_level=level,
            raw_source=TerminalColorSource.UNKNOWN,
            effective_source=TerminalColorSource.UNKNOWN,
            output_is_tty=None,
            explicitly_disabled=level is TerminalColorLevel.NONE,
        )


DEGRADED_COLOR_SUPPORT = TerminalColorSupport.fixed(TerminalColorLevel.UNKNOWN)


def resolve_color_render_level(level: TerminalColorLevel) -> TerminalColorLevel:
    """统一解析渲染目标色深，明确无色不启用降级颜色。"""
    return TerminalColorLevel.ANSI16 if level is TerminalColorLevel.UNKNOWN else level


def detect_terminal_color_support(
    environ: typing.Mapping[str, str] | None = None,
    *,
    identity: TerminalIdentity | None = None,
    output_stream: object | None = None,
) -> TerminalColorSupport:
    """按固定优先级解析标准输出的原始和有效色深。"""

    env = os.environ if environ is None else environ
    output_is_tty = None if output_stream is None else stream_is_tty(output_stream)

    if "FORCE_COLOR" in env:
        forced = _forced_color_level(str(env.get("FORCE_COLOR") or ""))
        return TerminalColorSupport(
            raw_level=forced,
            effective_level=forced,
            raw_source=TerminalColorSource.FORCE_COLOR,
            effective_source=TerminalColorSource.FORCE_COLOR,
            output_is_tty=output_is_tty,
            explicitly_disabled=forced is TerminalColorLevel.NONE,
        )
    if "NO_COLOR" in env:
        return TerminalColorSupport(
            raw_level=TerminalColorLevel.NONE,
            effective_level=TerminalColorLevel.NONE,
            raw_source=TerminalColorSource.NO_COLOR,
            effective_source=TerminalColorSource.NO_COLOR,
            output_is_tty=output_is_tty,
            explicitly_disabled=True,
        )

    raw_level, raw_source, terminal_is_dumb = _raw_color_level(env, output_is_tty)
    effective_level = raw_level
    effective_source = raw_source

    if output_is_tty is False:
        effective_level = TerminalColorLevel.NONE
        effective_source = TerminalColorSource.NON_TTY
    elif not terminal_is_dumb:
        terminal_identity = identity or detect_terminal_identity(env)
        if (
            terminal_identity.kind is TerminalKind.WINDOWS_TERMINAL
            and terminal_identity.source_variable == "WT_SESSION"
        ):
            effective_level = TerminalColorLevel.TRUECOLOR
            effective_source = TerminalColorSource.WINDOWS_TERMINAL
        elif (
            terminal_identity.kind is TerminalKind.WINDOWS_TERMINAL
            and raw_level is TerminalColorLevel.ANSI16
        ):
            effective_level = TerminalColorLevel.TRUECOLOR
            effective_source = TerminalColorSource.WINDOWS_TERMINAL

    return TerminalColorSupport(
        raw_level=raw_level,
        effective_level=effective_level,
        raw_source=raw_source,
        effective_source=effective_source,
        output_is_tty=output_is_tty,
    )


def detect_terminal_color_level(
    environ: typing.Mapping[str, str] | None = None,
    *,
    identity: TerminalIdentity | None = None,
    output_stream: object | None = None,
) -> TerminalColorLevel:
    """返回标准输出的有效色深。"""

    return detect_terminal_color_support(
        environ,
        identity=identity,
        output_stream=output_stream,
    ).effective_level


def stream_is_tty(stream: object) -> bool:
    """判断流是否连接到交互终端。"""

    isatty = getattr(stream, "isatty", None)
    try:
        return bool(callable(isatty) and isatty())
    except (OSError, ValueError):
        return False


def _raw_color_level(
    env: typing.Mapping[str, str],
    output_is_tty: bool | None,
) -> tuple[TerminalColorLevel, TerminalColorSource, bool]:
    """解析不含终端身份修正的标准色深信号。"""

    color_term = str(env.get("COLORTERM") or "").casefold()
    if color_term in {"truecolor", "24bit"}:
        return TerminalColorLevel.TRUECOLOR, TerminalColorSource.COLORTERM, False

    term = str(env.get("TERM") or "").casefold()
    if term == "dumb":
        return TerminalColorLevel.UNKNOWN, TerminalColorSource.TERM, True
    if any(token in term for token in ("truecolor", "24bit", "direct")):
        return TerminalColorLevel.TRUECOLOR, TerminalColorSource.TERM, False
    if "256color" in term:
        return TerminalColorLevel.ANSI256, TerminalColorSource.TERM, False
    if not term:
        return TerminalColorLevel.UNKNOWN, TerminalColorSource.UNKNOWN, False
    if output_is_tty is not False:
        return TerminalColorLevel.ANSI16, TerminalColorSource.TERM, False
    return TerminalColorLevel.UNKNOWN, TerminalColorSource.NON_TTY, False


def _forced_color_level(value: str) -> TerminalColorLevel:
    """把 FORCE_COLOR 值转换为明确色深。"""

    normalized = value.strip().casefold()
    if normalized == "0":
        return TerminalColorLevel.NONE
    if normalized in {"3", "truecolor", "24bit"}:
        return TerminalColorLevel.TRUECOLOR
    if normalized == "2":
        return TerminalColorLevel.ANSI256
    return TerminalColorLevel.ANSI16


if __name__ == '__main__':
    pass
