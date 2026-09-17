# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import sys
import typing
from contextlib import contextmanager

from prompt_toolkit.input import create_input
from prompt_toolkit.input.base import Input
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys


class _BoundedInputParser(Vt100Parser):
    """限制未完成的粘贴和控制序列，避免 SDK 在交付按键前无界累积。

    每次隐藏读取独占一个实例；只替换平台输入 adapter 的解析器，不改变 SDK 全局状态。
    """

    def __init__(self, emit: typing.Callable[[KeyPress], None], max_bytes: int) -> None:
        """绑定按键消费者及粘贴上限，结束标记另保留六字节预算。"""
        self._emit = emit
        self._limit = max_bytes
        self._pending_bytes = 0
        super().__init__(self._deliver)

    def _deliver(self, press: KeyPress) -> None:
        """完成一次解析后重置未交付字节预算。"""
        self._pending_bytes = 0
        self._emit(press)

    def _call_handler(self, key: str | Keys | tuple[Keys, ...], insert_text: str) -> None:
        """粘贴起始标记不计入正文预算。"""
        self._pending_bytes = 0
        super()._call_handler(key, insert_text)

    def feed(self, data: str) -> None:
        """逐字符约束未完成序列；粘贴未闭合时仍允许取消或结束输入。"""
        for character in data:
            if self._in_bracketed_paste and character in ("\x03", "\x04", "\x1a"):
                self.reset()
                self._paste_buffer = ""
                self._pending_bytes = 0
            self._pending_bytes += len(character.encode("utf-8"))
            limit = self._limit + 6 if self._in_bracketed_paste else 256
            if self._pending_bytes > limit:
                raise BufferError("Hidden terminal input exceeded its limit")
            super().feed(character)


def create_hidden_input(stream: typing.TextIO, *, max_bytes: int) -> Input:
    """创建独占平台 reader，在 SDK 内部缓存前安装有界解析器。"""
    terminal = create_input(stream)
    if sys.platform == "win32":
        from prompt_toolkit.input.win32 import (
            Vt100ConsoleInputReader,
            Win32Input,
        )

        if isinstance(terminal, Win32Input):
            reader = terminal.console_input_reader
            if isinstance(reader, Vt100ConsoleInputReader):
                reader._vt100_parser = _BoundedInputParser(reader._vt100_parser.feed_key_callback, max_bytes)
    else:
        from prompt_toolkit.input.vt100 import Vt100Input

        if isinstance(terminal, Vt100Input):
            terminal.vt100_parser = _BoundedInputParser(terminal.vt100_parser.feed_key_callback, max_bytes)
    return terminal


@contextmanager
def hidden_terminal_mode(stream: typing.TextIO) -> typing.Iterator[None]:
    """独占隐藏输入模式，失败不降级回显；退出丢弃剩余输入并严格恢复原始模式。

    调用方负责注销键盘监听后离开此范围，不得与另一终端 reader 共用 stdin。
    平台接口失败直接上抛，由前端转为不含输入内容的固定错误。
    """
    descriptor = stream.fileno()
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetConsoleMode.restype = wintypes.BOOL
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetConsoleMode.restype = wintypes.BOOL
        kernel.FlushConsoleInputBuffer.argtypes = [wintypes.HANDLE]
        kernel.FlushConsoleInputBuffer.restype = wintypes.BOOL
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        original_mode = wintypes.DWORD()
        if not kernel.GetConsoleMode(handle, ctypes.byref(original_mode)):
            raise OSError("Terminal input mode unavailable")
        # 保留既有 VT 输入选择，只关闭行缓冲、回显和系统 Ctrl+C 消费。
        if not kernel.SetConsoleMode(handle, original_mode.value & ~0x0007):
            raise OSError("Terminal input mode unavailable")
        try:
            yield
        finally:
            try:
                if not kernel.FlushConsoleInputBuffer(handle):
                    raise OSError("Terminal input cleanup failed")
            finally:
                if not kernel.SetConsoleMode(handle, original_mode.value):
                    raise OSError("Terminal input restoration failed")
    else:
        import termios

        original_attributes = termios.tcgetattr(descriptor)
        updated = copy.deepcopy(original_attributes)
        updated[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.IEXTEN | termios.ISIG)
        updated[0] &= ~(termios.IXON | termios.IXOFF | termios.ICRNL | termios.INLCR | termios.IGNCR)
        updated[6][termios.VMIN] = 1
        updated[6][termios.VTIME] = 0
        termios.tcsetattr(descriptor, termios.TCSANOW, updated)
        try:
            yield
        finally:
            try:
                termios.tcflush(descriptor, termios.TCIFLUSH)
            finally:
                termios.tcsetattr(descriptor, termios.TCSANOW, original_attributes)


if __name__ == '__main__':
    pass
