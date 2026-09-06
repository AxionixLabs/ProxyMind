# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing

from prompt_toolkit.input.base import Input
from prompt_toolkit.input.win32 import DWORD
from prompt_toolkit.input.win32 import EventTypes
from prompt_toolkit.input.win32 import INPUT_RECORD
from prompt_toolkit.input.win32 import KEY_EVENT_RECORD
from prompt_toolkit.input.win32 import Vt100ConsoleInputReader
from prompt_toolkit.input.win32 import Win32Input

_VK_MENU = 0x12


class WindowsConPtyInputReader(Vt100ConsoleInputReader):
    """读取 ConPTY 在 Alt key-up 事件中交付的 Unicode 字符。

    Windows ConPTY 会把不能表示为普通虚拟键的字符编码为 Alt 合成事件，
    最终 Unicode 码元只出现在 `VK_MENU` 的 key-up 记录中。实现方必须保留
    Vt100 reader 的解析与关闭生命周期，只扩展字符事件接收条件。
    """

    def _get_keys(
        self,
        read: DWORD,
        input_records: typing.Sequence[INPUT_RECORD],
    ) -> typing.Iterator[str]:
        """按 Console 事件顺序返回 VT 字符和 ConPTY Unicode 码元。"""
        for index in range(read.value):
            input_record = input_records[index]
            if input_record.EventType not in EventTypes:
                continue
            event = getattr(
                input_record.Event,
                EventTypes[input_record.EventType],
            )
            if not isinstance(event, KEY_EVENT_RECORD):
                continue
            char = event.uChar.UnicodeChar
            if char == "\x00":
                continue
            if event.KeyDown or event.VirtualKeyCode == _VK_MENU:
                yield char


def create_windows_input(stream: typing.TextIO) -> Input:
    """创建保留 ConPTY Unicode 事件的 Windows prompt_toolkit 输入。"""
    if sys.platform != "win32":
        raise RuntimeError("Windows terminal input is only available on win32")
    input_obj = Win32Input(stream)
    previous_reader = input_obj.console_input_reader
    if not isinstance(previous_reader, Vt100ConsoleInputReader):
        return input_obj
    input_obj.console_input_reader = WindowsConPtyInputReader()
    previous_reader.close()
    return input_obj


if __name__ == '__main__':
    pass
