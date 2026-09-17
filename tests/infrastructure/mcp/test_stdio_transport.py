import io
import sys

import anyio
import pytest

from anyio.streams.buffered import BufferedByteReceiveStream
from mcp.client.stdio import StdioServerParameters

from infrastructure.mcp.stdio_transport import (
    MAX_STDIO_FRAME_BYTES,
    bounded_stdio_client,
    read_stdio_frames,
)
from infrastructure.mcp.errors import flatten_exceptions


@pytest.mark.anyio
@pytest.mark.parametrize("suffix", [b"", b"\n"])
async def test_frame_limit_applies_before_decoding_and_without_newline(suffix) -> None:
    sender, receiver = anyio.create_memory_object_stream[bytes](200)
    async with sender, receiver:
        for _ in range(MAX_STDIO_FRAME_BYTES // 65536):
            await sender.send(b"x" * 65536)
        await sender.send(b"x" + suffix)
        await sender.aclose()
        with pytest.raises(ValueError, match="frame exceeds"):
            async for _ in read_stdio_frames(BufferedByteReceiveStream(receiver)):
                pytest.fail("Oversized frame was yielded")


@pytest.mark.anyio
async def test_framing_preserves_split_utf8_crlf_and_multiple_lines() -> None:
    sender, receiver = anyio.create_memory_object_stream[bytes](10)
    payload = '汉🙂'.encode()
    async with sender, receiver:
        for chunk in (payload[:1], payload[1:4], payload[4:] + b"\r\nsecond\nthird", b"\n"):
            await sender.send(chunk)
        await sender.aclose()
        frames = [bytes(frame) async for frame in read_stdio_frames(BufferedByteReceiveStream(receiver))]
    assert frames == [payload + b"\r", b"second", b"third"]


@pytest.mark.anyio
async def test_exact_limit_is_accepted_and_incomplete_tail_is_rejected() -> None:
    sender, receiver = anyio.create_memory_object_stream[bytes](2)
    async with sender, receiver:
        await sender.send(b"x" * MAX_STDIO_FRAME_BYTES + b"\npartial")
        await sender.aclose()
        frames = read_stdio_frames(BufferedByteReceiveStream(receiver))
        assert len(await anext(frames)) == MAX_STDIO_FRAME_BYTES
        with pytest.raises(ValueError, match="incomplete frame"):
            await anext(frames)


@pytest.mark.anyio
async def test_failed_spawn_closes_transport() -> None:
    with pytest.raises(ExceptionGroup) as raised:
        async with bounded_stdio_client(
            StdioServerParameters(command="nonexistent-mcp-test-executable"), errlog=sys.stderr,
        ):
            pytest.fail("Unexpected subprocess")
    assert all(isinstance(error, OSError) for error in flatten_exceptions(raised.value))


@pytest.mark.anyio
async def test_unsupported_line_encoding_is_rejected_before_spawn() -> None:
    with pytest.raises(ValueError, match="ASCII-compatible"):
        async with bounded_stdio_client(
            StdioServerParameters(command=sys.executable, encoding="utf-16"), errlog=io.StringIO(),
        ):
            pytest.fail("Unexpected subprocess")
