import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from agent.ports.desktop import DesktopError
from infrastructure.sidecars.desktop.session import (
    DesktopSidecarSession,
    create_desktop_session,
)


async def start_fixture(root: Path, mode: str, *, timeout: float = 5) -> DesktopSidecarSession:
    """启动版本化协议的独立进程测试替身。"""
    return await DesktopSidecarSession.start(
        (sys.executable, "-u", str(root / "tests" / "fixtures" / "desktop_sidecar.py"), mode),
        expected_platform="windows", timeout=timeout,
    )


@pytest.mark.anyio
async def test_handshake_concurrent_requests_stderr_and_close(repository_root: Path) -> None:
    session = await start_fixture(repository_root, "stderr")
    try:
        assert session.capabilities.platform == "windows"
        results = await asyncio.gather(*(session.list_windows() for _ in range(6)))
        assert all(not result.windows for result in results)
    finally:
        await session.close()
    assert len(session.stderr_tail.encode()) == 4096
    await session.close()
    with pytest.raises(DesktopError, match="closed"):
        await session.list_windows()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["wrong_version", "wrong_id", "macos"])
async def test_rejects_handshake_mismatch(repository_root: Path, mode: str) -> None:
    with pytest.raises(DesktopError) as failure:
        await start_fixture(repository_root, mode)
    assert failure.value.code == "protocol_error"


@pytest.mark.anyio
async def test_shared_transport_accepts_macos_capabilities(repository_root: Path) -> None:
    session = await DesktopSidecarSession.start(
        (sys.executable, "-u", str(repository_root / "tests/fixtures/desktop_sidecar.py"), "macos"),
        expected_platform="macos",
    )
    try:
        assert session.capabilities.coordinate_space == "logical_screen_points"
        assert not (await session.list_windows()).windows
        await session.validate_observation("window", "observation")
    finally:
        await session.close()


@pytest.mark.anyio
@pytest.mark.parametrize(("window", "observation"), [("", "token"), ("window", " "), ("window", "x" * 65)])
async def test_validation_rejects_bad_references_locally(repository_root: Path, window: str, observation: str) -> None:
    session = await start_fixture(repository_root, "stderr")
    try:
        with pytest.raises(ValueError):
            await session.validate_observation(window, observation)
        assert not (await session.list_windows()).windows
    finally:
        await session.close()


@pytest.mark.anyio
async def test_observation_coordinate_mismatch_closes_session(repository_root: Path) -> None:
    session = await start_fixture(repository_root, "wrong_coordinates")
    try:
        with pytest.raises(DesktopError, match="coordinate space mismatch"):
            await session.observe("window", screenshot=False)
        with pytest.raises(DesktopError, match="closed"):
            await session.list_windows()
    finally:
        await session.close()


@pytest.mark.anyio
@pytest.mark.parametrize(("mode", "code"), [
    ("eof", "transport_closed"), ("invalid_json", "protocol_error"),
    ("oversized", "protocol_error"), ("timeout", "timeout"),
])
async def test_failure_closes_session_without_replay(repository_root: Path, mode: str, code: str) -> None:
    session = await start_fixture(repository_root, mode, timeout=0.5 if mode == "timeout" else 5)
    try:
        with pytest.raises(DesktopError) as failure:
            await session.list_windows()
        assert failure.value.code == code
        with pytest.raises(DesktopError, match="closed"):
            await session.list_windows()
    finally:
        await session.close()


@pytest.mark.anyio
async def test_cancellation_closes_active_process(repository_root: Path) -> None:
    session = await start_fixture(repository_root, "timeout")
    task = asyncio.create_task(session.list_windows())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(DesktopError, match="closed"):
        await session.list_windows()
    await session.close()


@pytest.mark.anyio
async def test_missing_binary_and_unsupported_platform(tmp_path: Path) -> None:
    with pytest.raises(DesktopError) as failure:
        await create_desktop_session(tmp_path / "missing.exe", platform="win32")
    assert failure.value.code == "unavailable"
    with pytest.raises(DesktopError) as failure:
        await create_desktop_session(tmp_path / "missing", platform="linux")
    assert failure.value.code == "unsupported_platform"
    with pytest.raises(DesktopError) as failure:
        await DesktopSidecarSession.start((str(tmp_path / "missing.exe"),), expected_platform="windows")
    assert failure.value.code == "unavailable"


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
async def test_rejects_invalid_timeout_before_launch(repository_root: Path, timeout: float) -> None:
    with pytest.raises(ValueError):
        await start_fixture(repository_root, "stderr", timeout=timeout)


@pytest.mark.anyio
async def test_cancelled_close_still_reaps_process(repository_root: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(repository_root / "tests/fixtures/desktop_sidecar.py"), "slow_exit",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    session = DesktopSidecarSession(process, timeout=5)
    try:
        closing = asyncio.create_task(session.close())
        await asyncio.sleep(0.1)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 1)
        await asyncio.wait_for(asyncio.gather(session.close(), session.close()), 4)
        assert process.returncode is not None
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()


@pytest.mark.anyio
async def test_close_drains_backpressured_stdout(repository_root: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(repository_root / "tests/fixtures/desktop_sidecar.py"), "flood",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=1024, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    session = DesktopSidecarSession(process, timeout=5)
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(b'{"id":"hello","action":"hello"}\n')
        await process.stdin.drain()
        await process.stdout.readline()
        await asyncio.sleep(0.1)
        await asyncio.wait_for(session.close(), 4)
        assert process.returncode == 0
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()


@pytest.mark.anyio
async def test_close_interrupts_exchange_and_rejects_queued_request(repository_root: Path) -> None:
    session = await start_fixture(repository_root, "timeout")
    active = asyncio.create_task(session.list_windows())
    queued = asyncio.create_task(session.list_windows())
    try:
        await asyncio.sleep(0.1)
        await asyncio.wait_for(session.close(), 4)
        with pytest.raises(asyncio.CancelledError):
            await active
        with pytest.raises(DesktopError, match="closed"):
            await queued
    finally:
        await session.close()
        await asyncio.gather(active, queued, return_exceptions=True)
