import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from infrastructure.sidecars.desktop.session import create_desktop_session


@pytest.mark.anyio
async def test_glow_preview_shows_without_activation_and_closes(native_executable: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        str(native_executable), "--preview-glow", "4",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    observer = None
    try:
        assert process.stdout is not None
        started = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
        assert started["event"] == "glow_started"
        assert started["pointerWindows"] == started["monitors"] >= 1
        assert started["visibleWindows"] == started["monitors"] * 2
        assert started["foregroundUnchanged"] is True
        assert started["clickThrough"] is True
        observer = await create_desktop_session(native_executable)
        first = await observer.list_windows()
        glows = [window for window in first.windows if window.pid == process.pid and window.title == "Desktop visual preview"]
        pointers = [window for window in first.windows if window.pid == process.pid and window.title == "Desktop pointer preview"]
        assert len(glows) == len(pointers) == started["monitors"]
        for pointer in pointers:
            assert 0 < pointer.bounds.width / pointer.display_scale < 120
            assert 0 < pointer.bounds.height / pointer.display_scale < 120
        await asyncio.sleep(0.3)
        second = await observer.list_windows()
        by_reference = {window.reference: window for window in second.windows}
        assert all(by_reference[window.reference].bounds == window.bounds for window in glows)
        assert all(by_reference[window.reference].bounds != window.bounds for window in pointers)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0 and not stderr
        stopped = json.loads(stdout)
        assert stopped["event"] == "glow_stopped"
        assert stopped["reason"] == "timeout"
        assert stopped["visibleWindows"] == 0
        timing = stopped["frameTiming"]
        assert timing["samples"] > 0
        assert 0 < timing["meanMs"] <= timing["maximumMs"]
        assert 0 < timing["p95Ms"] <= timing["maximumMs"]
        assert timing["p95Ms"] <= timing["p99Ms"] <= timing["maximumMs"]
        assert 0 <= timing["over34Ms"] <= timing["samples"]
    finally:
        try:
            if observer is not None:
                await observer.close()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


@pytest.mark.anyio
@pytest.mark.parametrize("duration", ["0", "121", "invalid"])
async def test_glow_preview_rejects_invalid_duration(native_executable: Path, duration: str) -> None:
    process = await asyncio.create_subprocess_exec(
        str(native_executable), "--preview-glow", duration,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 2
        assert not stdout
        assert b"Expected --preview-glow" in stderr
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
