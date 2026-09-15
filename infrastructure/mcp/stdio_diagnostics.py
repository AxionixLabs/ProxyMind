# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import os
import select
import sys
import typing

if sys.platform == "win32":
    import msvcrt
    import win32pipe

from collections.abc import AsyncIterator

from infrastructure.mcp.errors import redact_external_text
from observability import observe

_READ_BYTES = 4096
_DRAIN_BYTES = 64 * 1024
_POLL_INTERVAL_SEC = 0.05


class _StderrLines:
    """在单连接生命周期内组装有界诊断行，不保存协议正文。"""

    def __init__(self, server: str, encoding: str) -> None:
        """绑定日志来源及子进程编码。"""
        self.server = server
        self.encoding = encoding
        self.pending = bytearray()
        self.truncated = False

    def feed(self, chunk: bytes) -> None:
        """组装跨读取边界的行，超长行只记录省略标记。"""
        segments = chunk.split(b"\n")
        for index, segment in enumerate(segments):
            remaining = _READ_BYTES - len(self.pending)
            self.pending.extend(segment[:remaining])
            self.truncated = self.truncated or len(segment) > remaining
            if index < len(segments) - 1:
                self.finish_line()

    def finish_line(self) -> None:
        """脱敏后记录完整行或关闭时的尾行，并释放行缓冲。"""
        if self.truncated:
            detail = "<oversized stderr line omitted>"
        else:
            detail = redact_external_text(
                self.pending.decode(self.encoding, errors="replace").strip()
            )
        if detail:
            observe(
                "external_mcp.stdio.stderr",
                level="INFO",
                server=self.server,
                detail=detail,
                truncated=self.truncated,
            )
        self.pending.clear()
        self.truncated = False


def _read_available(descriptor: int) -> bytes:
    """隔离匿名管道在 Windows 与 POSIX 上的非阻塞可读性检查。"""
    if sys.platform == "win32":
        _, available, _ = win32pipe.PeekNamedPipe(
            msvcrt.get_osfhandle(descriptor), 0,
        )
        if not isinstance(available, int):
            raise TypeError("pipe availability must be an integer")
        if not available:
            return b""
        return os.read(descriptor, min(available, _READ_BYTES))
    readable, _, _ = select.select([descriptor], [], [], 0)
    return os.read(descriptor, _READ_BYTES) if readable else b""


def _drain_stderr(descriptor: int, lines: _StderrLines) -> None:
    """每轮只读取有限字节，持续输出的子进程不能占满事件循环。"""
    for _ in range(_DRAIN_BYTES // _READ_BYTES):
        chunk = _read_available(descriptor)
        if not chunk:
            break
        lines.feed(chunk)


async def _read_stderr(descriptor: int, lines: _StderrLines) -> None:
    """在连接存续期间消费 stderr，不创建阻塞读取线程。"""
    while True:
        _drain_stderr(descriptor, lines)
        await asyncio.sleep(_POLL_INTERVAL_SEC)


@contextlib.asynccontextmanager
async def capture_stdio_stderr(
    server: str, encoding: str,
) -> AsyncIterator[typing.TextIO]:
    """为 SDK 提供 stderr 句柄；连接 owner 必须在关闭子进程后退出此上下文。

    读取任务和管道均由本上下文回收，关闭只排空有界数据，不等待后代进程的 EOF。
    """
    with contextlib.ExitStack() as stack:
        reader_fd, writer_fd = os.pipe()
        stack.callback(os.close, reader_fd)
        stack.callback(os.close, writer_fd)
        writer = stack.enter_context(os.fdopen(
            writer_fd, "w", encoding=encoding, errors="replace", closefd=False,
        ))
        lines = _StderrLines(server, encoding)
        reader = asyncio.create_task(
            _read_stderr(reader_fd, lines), name=f"MCP stderr {server}",
        )
        try:
            yield writer
        finally:
            reader.cancel()
            try:
                await asyncio.gather(reader, return_exceptions=True)
                if not reader.cancelled():
                    reader.result()
            finally:
                _drain_stderr(reader_fd, lines)
                lines.finish_line()


if __name__ == '__main__':
    pass
