# -*- coding: utf-8 -*-

import asyncio
import contextlib
import os
from unittest.mock import (
    Mock,
    call,
)

import pytest

from infrastructure.mcp import stdio_diagnostics
from infrastructure.mcp.stdio_diagnostics import capture_stdio_stderr


@pytest.mark.anyio
async def test_stderr_records_split_utf8_and_redacts_credentials(monkeypatch) -> None:
    observe = Mock()
    monkeypatch.setattr(stdio_diagnostics, "observe", observe)
    async with capture_stdio_stderr("playwright", "utf-8") as writer:
        descriptor = writer.fileno()
        raw = "正在安装".encode("utf-8")
        os.write(descriptor, raw[:2])
        await asyncio.sleep(0.1)
        os.write(descriptor, raw[2:] + b" token=private\n")
        os.write(descriptor, b"download https://user:secret@example.test/pkg?key=private\n")
        os.write(descriptor, b"Authorization: Bearer private\nlast diagnostic")
        os.write(descriptor, b'\n{"api_key": "private value"}\n\x1b[31mtoken\x1b[0m=private\n')
    assert observe.call_args_list == [
        call("external_mcp.stdio.stderr", level="INFO", server="playwright",
             detail="正在安装 token=<redacted>", truncated=False),
        call("external_mcp.stdio.stderr", level="INFO", server="playwright",
             detail="download https://example.test/pkg?<redacted>", truncated=False),
        call("external_mcp.stdio.stderr", level="INFO", server="playwright",
             detail="Authorization: <redacted> <redacted>", truncated=False),
        call("external_mcp.stdio.stderr", level="INFO", server="playwright",
             detail="last diagnostic", truncated=False),
        call("external_mcp.stdio.stderr", level="INFO", server="playwright",
             detail='{"api_key": <redacted>}', truncated=False),
        call("external_mcp.stdio.stderr", level="INFO", server="playwright",
             detail="token=<redacted>", truncated=False),
    ]


@pytest.mark.anyio
async def test_stderr_discards_oversized_line_and_continues_reading(monkeypatch) -> None:
    observe = Mock()
    monkeypatch.setattr(stdio_diagnostics, "observe", observe)
    async with capture_stdio_stderr("verbose", "utf-8") as writer:
        await asyncio.to_thread(
            os.write, writer.fileno(), b"x" * 100_000 + b" private\nnext line\n",
        )
    assert observe.call_args_list == [
        call("external_mcp.stdio.stderr", level="INFO", server="verbose",
             detail="<oversized stderr line omitted>", truncated=True),
        call("external_mcp.stdio.stderr", level="INFO", server="verbose",
             detail="next line", truncated=False),
    ]


@pytest.mark.anyio
async def test_stderr_cancellation_drains_tail_without_waiting_for_other_writers(monkeypatch) -> None:
    observe = Mock()
    monkeypatch.setattr(stdio_diagnostics, "observe", observe)
    with contextlib.ExitStack() as stack:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.1):
                async with capture_stdio_stderr("slow", "utf-8") as writer:
                    duplicate = os.dup(writer.fileno())
                    stack.callback(os.close, duplicate)
                    os.write(duplicate, b"still installing")
                    await asyncio.Event().wait()
        assert not any(
            task.get_name() == "MCP stderr slow" for task in asyncio.all_tasks()
        )
    observe.assert_called_once_with(
        "external_mcp.stdio.stderr", level="INFO", server="slow",
        detail="still installing", truncated=False,
    )
