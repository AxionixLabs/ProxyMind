# -*- coding: utf-8 -*-

import ctypes
from ctypes import wintypes

from .probe import (
    RgbColor,
    TerminalDefaultColors,
    TerminalProbeMethod,
    stream_file_descriptor,
)


class _Coord(ctypes.Structure):
    """映射 Windows COORD 结构。"""

    _fields_ = [("x", wintypes.SHORT), ("y", wintypes.SHORT)]


class _SmallRect(ctypes.Structure):
    """映射 Windows SMALL_RECT 结构。"""

    _fields_ = [
        ("left", wintypes.SHORT),
        ("top", wintypes.SHORT),
        ("right", wintypes.SHORT),
        ("bottom", wintypes.SHORT),
    ]


class _ScreenBufferInfo(ctypes.Structure):
    """映射 Windows CONSOLE_SCREEN_BUFFER_INFOEX 结构。"""

    _fields_ = [
        ("cbSize", wintypes.ULONG),
        ("dwSize", _Coord),
        ("dwCursorPosition", _Coord),
        ("wAttributes", wintypes.WORD),
        ("srWindow", _SmallRect),
        ("dwMaximumWindowSize", _Coord),
        ("wPopupAttributes", wintypes.WORD),
        ("bFullscreenSupported", wintypes.BOOL),
        ("ColorTable", wintypes.DWORD * 16),
    ]


def query_windows_default_colors(
    input_stream: object,
    output_stream: object,
    timeout: float,
) -> TerminalDefaultColors:
    """通过 Windows Console API 查询颜色且不读取 stdin。"""

    del input_stream, timeout
    return _windows_palette_colors(output_stream)


def _windows_handle(stream: object) -> int | None:
    """读取 Python 流对应的 Windows 原生句柄。"""

    import msvcrt

    descriptor = stream_file_descriptor(stream)
    if descriptor is None:
        return None
    try:
        handle = int(msvcrt.get_osfhandle(descriptor))
    except (OSError, ValueError):
        return None
    return handle if handle != -1 else None


def _windows_palette_colors(output_stream: object) -> TerminalDefaultColors:
    """从 Windows 控制台调色板读取当前默认颜色。"""

    handle = _windows_handle(output_stream)
    if handle is None:
        return TerminalDefaultColors(
            attempted=True,
            method=TerminalProbeMethod.WINDOWS_CONSOLE,
        )

    info = _ScreenBufferInfo()
    info.cbSize = ctypes.sizeof(info)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_screen_buffer_info = kernel32["GetConsoleScreenBufferInfoEx"]
    get_screen_buffer_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ScreenBufferInfo),
    ]
    get_screen_buffer_info.restype = wintypes.BOOL

    if not get_screen_buffer_info(
        wintypes.HANDLE(handle),
        ctypes.pointer(info),
    ):
        return TerminalDefaultColors(
            attempted=True,
            method=TerminalProbeMethod.WINDOWS_CONSOLE,
        )

    foreground_index = info.wAttributes & 0x0F
    background_index = (info.wAttributes >> 4) & 0x0F
    return TerminalDefaultColors(
        foreground=_colorref_rgb(info.ColorTable[foreground_index]),
        background=_colorref_rgb(info.ColorTable[background_index]),
        attempted=True,
        method=TerminalProbeMethod.WINDOWS_CONSOLE,
    )


def _colorref_rgb(value: int) -> RgbColor:
    """把 Windows COLORREF 转换为 RGB 元组。"""

    return value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF


if __name__ == '__main__':
    pass
