# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import threading
import typing
from dataclasses import dataclass
from enum import Enum

RgbColor: typing.TypeAlias = tuple[int, int, int]

TERMINAL_QUERY_TIMEOUT_SEC = 0.1
MAX_OSC_RESPONSE_BYTES = 1024


class TerminalProbeMethod(str, Enum):
    """描述默认颜色所使用的平台探测方法。"""

    UNIX_OSC = "unix_osc"
    WINDOWS_CONSOLE = "windows_console"
    CUSTOM = "custom"
    NOT_ATTEMPTED = "not_attempted"


@dataclass(frozen=True)
class TerminalDefaultColors:
    """保存一次终端默认前景和背景颜色探测的结果。"""

    foreground: RgbColor | None = None
    background: RgbColor | None = None
    attempted: bool = False
    method: TerminalProbeMethod = TerminalProbeMethod.NOT_ATTEMPTED

    @property
    def complete(self) -> bool:
        """判断默认前景和背景是否成对存在。"""

        return self.foreground is not None and self.background is not None


@dataclass(frozen=True)
class _OscColorResponse:
    """描述输入缓冲区内一个完整的 OSC 10/11 响应。"""

    start: int
    end: int
    command: int
    payload: bytes


DefaultColorsProbe: typing.TypeAlias = typing.Callable[
    [object, object, float],
    TerminalDefaultColors,
]


class TerminalDefaultColorsCache(object):
    """缓存一次 TUI 会话的默认终端颜色探测结果。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempted = False
        self._colors = TerminalDefaultColors()

    def get_or_probe(
        self,
        input_stream: object,
        output_stream: object,
        timeout: float,
        probe: DefaultColorsProbe,
    ) -> TerminalDefaultColors:
        """首次调用执行探测，后续调用复用同一结果。"""

        with self._lock:
            if self._attempted:
                return self._colors
            self._attempted = True
            try:
                result = probe(input_stream, output_stream, timeout)
                if isinstance(result, TerminalDefaultColors):
                    method = result.method
                    if method is TerminalProbeMethod.NOT_ATTEMPTED:
                        method = TerminalProbeMethod.CUSTOM
                    self._colors = TerminalDefaultColors(
                        foreground=result.foreground if result.complete else None,
                        background=result.background if result.complete else None,
                        attempted=True,
                        method=method,
                    )
                else:
                    self._colors = TerminalDefaultColors(attempted=True)
            except (AttributeError, OSError, TypeError, ValueError, RuntimeError):
                self._colors = TerminalDefaultColors(attempted=True)
            return self._colors


def query_terminal_default_colors(
    input_stream: object,
    output_stream: object,
    timeout: float = TERMINAL_QUERY_TIMEOUT_SEC,
    *,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalDefaultColors:
    """通过当前平台 adapter 查询终端默认颜色。"""

    if sys.platform == "win32":
        from .probe_windows import query_windows_default_colors

        return query_windows_default_colors(input_stream, output_stream, timeout)

    from .probe_unix import query_unix_default_colors

    return query_unix_default_colors(
        input_stream,
        output_stream,
        timeout,
        input_replay,
    )


def parse_terminal_color_responses(data: bytes | str) -> TerminalDefaultColors:
    """解析完整 OSC 10/11 响应中的默认前景色和背景色。"""

    raw = data.encode("ascii", errors="ignore") if isinstance(data, str) else data
    colors: dict[int, RgbColor] = {}
    for response in _color_responses(raw):
        color = _parse_osc_color(response.payload.decode("ascii", errors="ignore"))
        if color is not None:
            colors[response.command] = color
    return TerminalDefaultColors(
        foreground=colors.get(10),
        background=colors.get(11),
        attempted=True,
        method=TerminalProbeMethod.UNIX_OSC,
    )


def filter_terminal_color_responses(data: bytes) -> bytes:
    """移除完整 OSC 10/11 响应并保留其余输入字节。"""

    responses = _color_responses(data)
    if not responses:
        return data

    replay = bytearray()
    cursor = 0
    for response in responses:
        replay.extend(data[cursor:response.start])
        cursor = response.end
    replay.extend(data[cursor:])
    return bytes(replay)


def stream_file_descriptor(stream: object) -> int | None:
    """在对象边界校验并读取 Python 流的文件描述符。"""

    fileno = getattr(stream, "fileno", None)
    if not callable(fileno):
        return None
    try:
        return int(fileno())
    except (OSError, TypeError, ValueError):
        return None


def _color_responses(data: bytes) -> tuple[_OscColorResponse, ...]:
    """定位有界且完整的 OSC 10/11 响应。"""

    responses: list[_OscColorResponse] = []
    cursor = 0
    while cursor < len(data):
        start = data.find(b"\x1b]", cursor)
        if start < 0:
            break
        separator = data.find(b";", start + 2, start + 5)
        if separator < 0:
            cursor = start + 1
            continue
        command_bytes = data[start + 2:separator]
        if command_bytes not in {b"10", b"11"}:
            cursor = start + 1
            continue

        payload_start = separator + 1
        search_end = min(len(data), payload_start + MAX_OSC_RESPONSE_BYTES + 2)
        terminator = _osc_terminator(data, payload_start, search_end)
        if terminator is None:
            cursor = start + 1
            continue
        payload_end, response_end = terminator
        responses.append(_OscColorResponse(
            start=start,
            end=response_end,
            command=int(command_bytes),
            payload=data[payload_start:payload_end],
        ))
        cursor = response_end
    return tuple(responses)


def _osc_terminator(
    data: bytes,
    start: int,
    end: int,
) -> tuple[int, int] | None:
    """在有界范围内寻找 BEL 或 ST 终止符。"""

    cursor = start
    while cursor < end:
        value = data[cursor]
        if value == 0x07:
            return cursor, cursor + 1
        if value == 0x1B:
            if cursor + 1 < end and data[cursor + 1] == 0x5C:
                return cursor, cursor + 2
            return None
        cursor += 1
    return None


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


if __name__ == '__main__':
    pass
