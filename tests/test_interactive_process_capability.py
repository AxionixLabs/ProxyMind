import asyncio
import os
import sys
from pathlib import Path

import pytest

from agent.ports import InteractiveProcessHandle
from agent.ports import InteractiveProcessSpec
from agent.ports import TerminalSize
from infrastructure.platform.pty import LocalInteractiveProcessCapability


pytestmark = pytest.mark.pty_acceptance


async def _wait_for_text(
    output: bytearray,
    changed: asyncio.Event,
    expected: str,
    *,
    timeout: float = 5.0,
) -> None:
    expected_bytes = expected.encode("utf-8")
    deadline = asyncio.get_running_loop().time() + timeout
    while expected_bytes not in output:
        changed.clear()
        if expected_bytes in output:
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(
                f"timed out waiting for {expected!r}: "
                f"{bytes(output).decode('utf-8', errors='replace')!r}"
            )
        await asyncio.wait_for(changed.wait(), timeout=remaining)


async def _collect_output(
    handle: InteractiveProcessHandle,
    output: bytearray,
    changed: asyncio.Event,
) -> None:
    async for chunk in handle.read_output():
        output.extend(chunk)
        changed.set()


def test_terminal_size_and_spec_are_validated() -> None:
    with pytest.raises(ValueError, match="rows"):
        TerminalSize(rows=0)
    with pytest.raises(TypeError, match="columns"):
        TerminalSize(columns=True)

    environment = {"PTY_VALUE": "before"}
    spec = InteractiveProcessSpec(
        argv=(sys.executable, "-V"),
        cwd=Path.cwd(),
        env=environment,
    )
    environment["PTY_VALUE"] = "after"

    assert spec.env["PTY_VALUE"] == "before"
    with pytest.raises(TypeError):
        spec.env["PTY_VALUE"] = "changed"


@pytest.mark.anyio
async def test_local_interactive_process_supports_input_resize_and_output() -> None:
    capability = LocalInteractiveProcessCapability()
    source = (
        "import shutil; value=input('READY>'); "
        "size=shutil.get_terminal_size(); "
        "print(f'VALUE={value};SIZE={size.lines}x{size.columns}', flush=True)"
    )
    handle = await capability.spawn(InteractiveProcessSpec(
        argv=(sys.executable, "-u", "-c", source),
        cwd=Path.cwd(),
        env=os.environ,
        size=TerminalSize(rows=24, columns=80),
    ))
    output = bytearray()
    changed = asyncio.Event()
    reader = asyncio.create_task(_collect_output(handle, output, changed))
    try:
        await _wait_for_text(output, changed, "READY>")
        await handle.resize(TerminalSize(rows=31, columns=101))
        await handle.write("hello\r")
        assert await handle.wait() == 0
        await asyncio.wait_for(reader, timeout=3.0)
        assert "VALUE=hello;SIZE=31x101" in output.decode(
            "utf-8",
            errors="replace",
        )
    finally:
        await handle.aclose()
        await capability.aclose()
        if not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.anyio
async def test_local_interactive_process_interrupt_is_not_terminate() -> None:
    capability = LocalInteractiveProcessCapability()
    if os.name == "nt":
        source = (
            "import ctypes,sys; from ctypes import wintypes; "
            "kernel32=ctypes.WinDLL('kernel32',use_last_error=True); "
            "stream=kernel32.GetStdHandle(-10); mode=wintypes.DWORD(); "
            "assert kernel32.GetConsoleMode(stream,ctypes.byref(mode)); "
            "assert kernel32.SetConsoleMode(stream,mode.value & ~7); "
            "print('WAITING',flush=True); value=sys.stdin.buffer.read(1); "
            "print(f'BYTE={value[0]}',flush=True); print('AFTER',flush=True)"
        )
        interrupted = "BYTE=3"
    else:
        source = (
            "import time; print('WAITING',flush=True); "
            "\ntry:\n while True: time.sleep(1)"
            "\nexcept KeyboardInterrupt:\n print('INTERRUPTED',flush=True)"
            "\nprint('AFTER',flush=True)"
        )
        interrupted = "INTERRUPTED"
    handle = await capability.spawn(InteractiveProcessSpec(
        argv=(sys.executable, "-u", "-c", source),
        cwd=Path.cwd(),
        env=os.environ,
    ))
    output = bytearray()
    changed = asyncio.Event()
    reader = asyncio.create_task(_collect_output(handle, output, changed))
    try:
        await _wait_for_text(output, changed, "WAITING")
        await handle.interrupt()
        await _wait_for_text(output, changed, interrupted)
        await _wait_for_text(output, changed, "AFTER")
        assert await handle.wait() == 0
        await asyncio.wait_for(reader, timeout=3.0)
    finally:
        await handle.aclose()
        await capability.aclose()
        if not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
