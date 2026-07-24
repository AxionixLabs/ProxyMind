# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing

TerminalKind = typing.Literal[
    "windows_terminal",
    "iterm2",
    "apple_terminal",
    "unknown",
]

OSC_PROGRESS_CLEAR         = "\x1b]9;4;0\x07"
OSC_PROGRESS_INDETERMINATE = "\x1b]9;4;3\x07"
OSC_PROGRESS_WARNING       = "\x1b]9;4;4;100\x07"


class TerminalProgress(typing.Protocol):
    """描述终端窗口进度状态。"""

    def begin(self) -> None:
        """进入不确定进度状态。"""
        ...

    def warning(self) -> None:
        """进入警告进度状态。"""
        ...

    def clear(self) -> None:
        """清除终端窗口进度状态。"""
        ...


class PassiveTerminalProgress(object):
    """提供不支持窗口进度的空实现。"""

    def begin(self) -> None:
        """忽略进度开始请求。"""
        return None

    def warning(self) -> None:
        """忽略警告状态请求。"""
        return None

    def clear(self) -> None:
        """忽略进度清理请求。"""
        return None


class OscTerminalProgress(object):
    """通过 OSC 9;4 维护终端窗口进度状态。"""

    def __init__(self, stream: typing.TextIO) -> None:
        self.stream = stream
        self._sequence: str | None = None

    def begin(self) -> None:
        """进入不确定进度状态。"""
        self._write(OSC_PROGRESS_INDETERMINATE)

    def warning(self) -> None:
        """进入完整警告进度状态。"""
        self._write(OSC_PROGRESS_WARNING)

    def clear(self) -> None:
        """清除已设置的终端窗口进度状态。"""
        if self._sequence is None:
            return None
        self._write(OSC_PROGRESS_CLEAR)
        self._sequence = None

    def _write(self, sequence: str) -> None:
        """写入发生变化的控制序列。"""
        if sequence == self._sequence:
            return None
        self.stream.write(sequence)
        self.stream.flush()
        self._sequence = sequence


def terminal_kind(
    environ: typing.Mapping[str, str] | None = None,
) -> TerminalKind:
    """根据终端环境变量识别已知终端。"""
    env = os.environ if environ is None else environ
    if env.get("WT_SESSION"):
        return "windows_terminal"

    term_program = env.get("TERM_PROGRAM")
    if term_program == "iTerm.app":
        return "iterm2"
    if term_program == "Apple_Terminal":
        return "apple_terminal"
    return "unknown"


def supports_osc_progress(
    stream: typing.TextIO,
    environ: typing.Mapping[str, str] | None = None,
) -> bool:
    """判断输出流和终端是否支持 OSC 9;4。"""
    isatty = getattr(stream, "isatty", None)
    try:
        interactive = bool(callable(isatty) and isatty())
    except (OSError, ValueError):
        interactive = False
    return interactive and terminal_kind(environ) in {
        "windows_terminal",
        "iterm2",
    }


def create_terminal_progress(
    stream: typing.TextIO,
    environ: typing.Mapping[str, str] | None = None,
) -> TerminalProgress:
    """为当前终端创建窗口进度实现。"""
    if supports_osc_progress(stream, environ):
        return OscTerminalProgress(stream)
    return PassiveTerminalProgress()


if __name__ == '__main__':
    pass
