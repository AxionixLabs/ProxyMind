# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import typing

import anyio

from collections.abc import AsyncIterator

from anyio.abc import ByteReceiveStream
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
from mcp import types as mcp_types
from mcp.client.stdio import (
    StdioServerParameters,
    get_default_environment,
)
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

from infrastructure.platform.stdio_process import open_stdio_process

MAX_STDIO_FRAME_BYTES = 8 * 1024 * 1024
_READ_BYTES = 64 * 1024


async def read_stdio_frames(stream: ByteReceiveStream) -> AsyncIterator[bytearray]:
    """在解码前按字节限制单行，分块输入和没有换行的输入共用同一上限。"""
    pending = bytearray()
    while True:
        try:
            chunk = await stream.receive(_READ_BYTES)
        except anyio.EndOfStream:
            if pending:
                raise ValueError("MCP stdio closed with an incomplete frame") from None
            return
        start = 0
        while start < len(chunk):
            end = chunk.find(b"\n", start)
            stop = len(chunk) if end == -1 else end
            if len(pending) + stop - start > MAX_STDIO_FRAME_BYTES:
                raise ValueError(f"MCP stdio frame exceeds {MAX_STDIO_FRAME_BYTES} bytes")
            pending.extend(chunk[start:stop])
            if end == -1:
                break
            yield pending
            pending = bytearray()
            start = end + 1


@contextlib.asynccontextmanager
async def bounded_stdio_client(
    server: StdioServerParameters, *, errlog: typing.TextIO,
) -> AsyncIterator[tuple[
    MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage],
]]:
    """以有界帧替换 SDK 无界文本缓冲；连接 owner 仍独占进入、取消及退出生命周期。"""
    if "\n".encode(server.encoding) != b"\n":
        raise ValueError("MCP stdio requires an ASCII-compatible encoding")
    incoming, read = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write, outgoing = anyio.create_memory_object_stream[SessionMessage](0)
    async with incoming, read, write, outgoing, anyio.create_task_group() as tasks:
        async with open_stdio_process(
            server.command, server.args, env={**get_default_environment(), **(server.env or {})},
            cwd=server.cwd, stderr=errlog,
        ) as (stdout, stdin):
            async def receive() -> None:
                """将完整且有界的帧交给 SDK，失败只转交固定诊断，避免记录协议正文。"""
                async with incoming:
                    try:
                        async for frame in read_stdio_frames(stdout):
                            try:
                                message = mcp_types.JSONRPCMessage.model_validate_json(
                                    frame.decode(server.encoding, errors=server.encoding_error_handler),
                                )
                            except (UnicodeError, ValidationError):
                                raise ValueError("MCP stdio received an invalid JSON-RPC frame") from None
                            await incoming.send(SessionMessage(message))
                    except (ValueError, anyio.BrokenResourceError) as error:
                        await incoming.send(error)

            async def send() -> None:
                """逐条写入 SDK 消息，保留既有序列化和背压。"""
                async with outgoing:
                    async for message in outgoing:
                        payload = message.message.model_dump_json(by_alias=True, exclude_none=True)
                        await stdin.send((payload + "\n").encode(
                            server.encoding, errors=server.encoding_error_handler,
                        ))

            tasks.start_soon(receive)
            tasks.start_soon(send)
            try:
                yield read, write
            finally:
                tasks.cancel_scope.cancel()


if __name__ == '__main__':
    pass
