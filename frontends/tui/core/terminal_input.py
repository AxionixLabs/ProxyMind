# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import os
import sys

from prompt_toolkit.input.base import Input
from prompt_toolkit.input.typeahead import clear_typeahead


def clear_pending_input(input_obj: Input) -> None:
    """清掉 prompt 边界上的排队按键，避免带入下一轮输入。"""
    with contextlib.suppress(Exception):
        clear_typeahead(input_obj)

    with contextlib.suppress(Exception):
        input_obj.flush_keys()

    if sys.platform == "win32":
        _flush_win32_console_input(input_obj)
    else:
        _flush_posix_tty_input(input_obj)


def _flush_posix_tty_input(input_obj: Input) -> None:
    """清 POSIX TTY 内核输入队列。"""
    with contextlib.suppress(Exception):
        import termios

        fd = input_obj.fileno()
        if os.isatty(fd):
            termios.tcflush(fd, termios.TCIFLUSH)


def _flush_win32_console_input(input_obj: Input) -> None:
    """清 Windows Console 输入队列。"""
    with contextlib.suppress(Exception):
        import ctypes
        from ctypes import wintypes

        handle = getattr(input_obj, "handle", None)

        handle_value = getattr(handle, "value", handle)
        if handle_value is None:
            return None

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        flush_console_input_buffer = kernel32.FlushConsoleInputBuffer
        flush_console_input_buffer.argtypes = [wintypes.HANDLE]
        flush_console_input_buffer.restype = wintypes.BOOL
        flush_console_input_buffer(handle)


if __name__ == '__main__':
    pass
