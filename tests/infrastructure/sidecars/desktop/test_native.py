import asyncio
import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from agent.ports.desktop import DesktopError
from infrastructure.sidecars.desktop.session import create_desktop_session


@pytest.mark.anyio
async def test_native_protocol_errors_and_eof(native_executable: Path) -> None:
    hello = b'{"v":1,"id":"once","action":"hello","parameters":{}}\n'
    process = await asyncio.create_subprocess_exec(
        str(native_executable), "--parent-pid", str(os.getpid()), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(hello + hello + b'{"v":1,"v":1}\n'), 10)
    responses = [json.loads(line) for line in stdout.splitlines()]
    assert process.returncode == 0
    diagnostics = [json.loads(line) for line in stderr.splitlines()]
    assert [item["code"] for item in diagnostics] == ["duplicate_request", "invalid_request"]
    assert all(set(item) == {"event", "id", "action", "phase", "code"} for item in diagnostics)
    assert [item["phase"] for item in diagnostics] == ["communication", "communication"]
    assert responses[0]["result"]["platform"] == "windows"
    assert responses[1]["error"]["code"] == "duplicate_request"
    assert responses[2]["error"]["code"] == "invalid_request"
    for broken in (b'{"v":1', b"x" * 65536 + b"\n"):
        process = await asyncio.create_subprocess_exec(
            str(native_executable), "--parent-pid", str(os.getpid()), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(broken), 10)
        assert process.returncode == 1 and not stdout
    failure = json.loads(stderr)
    assert failure["event"] == "desktop_failure" and failure["phase"] == "communication"


@pytest.mark.anyio
async def test_real_window_tree_capture_and_close(repository_root: Path, native_executable: Path, tmp_path: Path) -> None:
    fixture_executable = repository_root / "tests/fixtures/cua_windows/bin/Release/net10.0-windows/Fixture.exe"
    assert fixture_executable.is_file(), "Build the native GUI fixture first"
    fixture = await asyncio.create_subprocess_exec(str(fixture_executable), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    session = None
    try:
        session = await create_desktop_session(native_executable)
        assert session.capabilities.capture_supported
        window = None
        for _ in range(30):
            listing = await session.list_windows()
            window = next((item for item in listing.windows if item.pid == fixture.pid), None)
            if window is not None:
                break
            await asyncio.sleep(0.1)
        assert window is not None, "Fixture did not create a visible window"
        state = await session.observe(window.reference, screenshot=True)
        assert state.tree_error is None
        assert any(control.name == "Increment counter" for control in state.controls)
        assert state.image is not None
        image = Image.open(io.BytesIO(state.image.content))
        image.load()
        assert image.size == (state.image.width, state.image.height)
        assert image.width >= 600 and image.height >= 320
        assert image.convert("RGB").getpixel((40, image.height - 40)) == (22, 38, 65)
        image.save(tmp_path / "native-fixture.png")
        print(f"Verified native screenshot: {tmp_path / 'native-fixture.png'}")
        no_image = await session.observe(window.reference, screenshot=False)
        assert no_image.image is None
        assert no_image.reference != state.reference
        fixture.terminate()
        await fixture.wait()
        with pytest.raises(DesktopError) as failure:
            await session.observe(window.reference, screenshot=False)
        assert failure.value.code == "stale_window"
    finally:
        try:
            if session is not None:
                await session.close()
        finally:
            if fixture.returncode is None:
                fixture.terminate()
                await fixture.wait()


@pytest.mark.anyio
async def test_native_fragmented_utf8_batched_frames_and_exact_limit(native_executable: Path) -> None:
    first = json.dumps({"v": 1, "id": "分片", "action": "hello", "parameters": {}}, ensure_ascii=False).encode() + b"\n"
    boundary = b'{"v":1,"id":"boundary","action":"hello","parameters":{}}\n'
    boundary = boundary[:-1] + b" " * (65536 - len(boundary)) + b"\n"
    last = b'{"v":1,"id":"last","action":"hello","parameters":{}}\n'
    payload = first + boundary + last
    split = payload.index("分".encode()) + 1
    process = await asyncio.create_subprocess_exec(
        str(native_executable), "--parent-pid", str(os.getpid()), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(payload[:split])
        await process.stdin.drain()
        await asyncio.sleep(0.05)
        stdout, stderr = await asyncio.wait_for(process.communicate(payload[split:]), 10)
        assert process.returncode == 0 and not stderr
        replies = [json.loads(line) for line in stdout.splitlines()]
        assert [reply["id"] for reply in replies] == ["分片", "boundary", "last"]
        assert all(reply["ok"] and reply["result"]["platform"] == "windows" for reply in replies)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.anyio
async def test_release_guard_holds_lease_until_eof_without_generating_input(native_executable: Path) -> None:
    processes: list[asyncio.subprocess.Process] = []
    try:
        for _ in range(2):
            processes.append(await asyncio.create_subprocess_exec(str(native_executable), "--release-input",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW))

        async def command(process: asyncio.subprocess.Process, text: str) -> bytes:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(text.encode() + b"\n")
            await process.stdin.drain()
            return (await asyncio.wait_for(process.stdout.readline(), 3)).strip()

        first, second = processes
        assert await command(first, "begin") == b"ok"
        assert await command(second, "begin") == b"busy"
        assert first.stdin is not None
        first.stdin.close()
        await asyncio.wait_for(first.wait(), 3)
        assert first.returncode == 0
        assert await command(second, "begin") == b"ok"
        assert await command(second, "end") == b"ok"
    finally:
        for process in processes:
            if process.stdin is not None:
                process.stdin.close()
            await asyncio.wait_for(process.communicate(), 3)
