# -*- coding: utf-8 -*-

import os
import select
import time
import typing

from .probe import (
    TerminalDefaultColors,
    TerminalProbeMethod,
    filter_terminal_color_responses,
    parse_terminal_color_responses,
    stream_file_descriptor,
)

MAX_STARTUP_PROBE_BYTES = 64 * 1024
_READ_CHUNK_BYTES = 4096


def query_unix_default_colors(
    input_stream: object,
    output_stream: object,
    timeout: float,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalDefaultColors:
    """通过 Unix TTY 查询默认颜色并恢复全部描述符状态。"""

    import termios

    read_fd: int | None = None
    write_fd: int | None = None
    tty_fd: int | None = None
    original_attributes = None
    original_blocking: bool | None = None

    try:
        input_fd = stream_file_descriptor(input_stream)
        output_fd = stream_file_descriptor(output_stream)
        if (
            input_fd is not None
            and output_fd is not None
            and os.isatty(input_fd)
            and os.isatty(output_fd)
        ):
            read_fd = os.dup(input_fd)
            write_fd = os.dup(output_fd)
        else:
            tty_fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            read_fd = os.dup(tty_fd)
            write_fd = os.dup(tty_fd)

        original_attributes = termios.tcgetattr(read_fd)
        original_blocking = os.get_blocking(read_fd)
        raw_attributes = list(original_attributes)
        raw_attributes[3] &= ~(termios.ICANON | termios.ECHO)
        control_characters = list(raw_attributes[6])
        control_characters[termios.VMIN] = 0
        control_characters[termios.VTIME] = 0
        raw_attributes[6] = control_characters

        termios.tcsetattr(read_fd, termios.TCSANOW, raw_attributes)
        os.set_blocking(read_fd, False)
        _write_terminal_query(write_fd)
        return _read_unix_color_response(read_fd, timeout, input_replay)
    finally:
        if read_fd is not None and original_attributes is not None:
            try:
                termios.tcsetattr(read_fd, termios.TCSANOW, original_attributes)
            except OSError:
                pass
        if read_fd is not None and original_blocking is not None:
            try:
                os.set_blocking(read_fd, original_blocking)
            except OSError:
                pass
        for descriptor in (read_fd, write_fd, tty_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _read_unix_color_response(
    read_fd: int,
    timeout: float,
    input_replay: typing.Callable[[bytes], None] | None = None,
) -> TerminalDefaultColors:
    """在共享截止时间和固定内存上限内收集颜色响应。"""

    deadline = time.monotonic() + max(0.0, timeout)
    buffer = bytearray()

    while len(buffer) < MAX_STARTUP_PROBE_BYTES and time.monotonic() < deadline:
        remaining_time = max(0.0, deadline - time.monotonic())
        readable, _, _ = select.select([read_fd], [], [], remaining_time)
        if not readable:
            break

        remaining_bytes = MAX_STARTUP_PROBE_BYTES - len(buffer)
        try:
            chunk = os.read(read_fd, min(_READ_CHUNK_BYTES, remaining_bytes))
        except BlockingIOError:
            continue
        if not chunk:
            break
        buffer.extend(chunk)

        colors = parse_terminal_color_responses(bytes(buffer))
        if colors.complete:
            return _finish_probe(buffer, colors, input_replay)

    colors = parse_terminal_color_responses(bytes(buffer))
    if not colors.complete:
        colors = TerminalDefaultColors(
            attempted=True,
            method=TerminalProbeMethod.UNIX_OSC,
        )
    return _finish_probe(buffer, colors, input_replay)


def _finish_probe(
    buffer: bytearray,
    colors: TerminalDefaultColors,
    input_replay: typing.Callable[[bytes], None] | None,
) -> TerminalDefaultColors:
    """过滤终端响应并把探测期间的用户输入回放一次。"""

    replay = filter_terminal_color_responses(bytes(buffer))
    if input_replay is not None and replay:
        input_replay(replay)
    return colors


def _terminal_color_query() -> bytes:
    """生成默认前景色和背景色的 OSC 查询。"""

    return b"\x1b]10;?\x1b\\\x1b]11;?\x1b\\"


def _write_terminal_query(write_fd: int) -> None:
    """完整写入短 OSC 查询并拒绝无进展的短写。"""

    query = _terminal_color_query()
    written = 0
    while written < len(query):
        count = os.write(write_fd, query[written:])
        if count <= 0:
            raise OSError("terminal query write made no progress")
        written += count


if __name__ == '__main__':
    pass
