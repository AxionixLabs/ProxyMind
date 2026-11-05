import asyncio
import base64
import io
import json
import subprocess
import typing
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

from agent.ports.desktop import (
    DesktopError,
    DesktopWindow,
)
from agent.protocol.json_value import ThawedJsonValue
from infrastructure.sidecars.desktop.session import (
    DesktopSidecarSession,
    create_desktop_session,
)
from infrastructure.sidecars.desktop.factory import DesktopSidecarFactory
from tests.fixtures.computer import computer_harness


@dataclass
class ControlledWindow:
    process: asyncio.subprocess.Process

    async def exchange(self, command: str) -> bytes:
        """只向专用验收窗口发送夹具命令，并读取确认结果。"""
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(command.encode() + b"\n")
        await self.process.stdin.drain()
        return await asyncio.wait_for(self.process.stdout.readline(), 5)

    async def command(self, command: str) -> None:
        """等待专用窗口完成状态变更，不生成系统输入。"""
        assert (await self.exchange(command)).strip() == b"ok"

    async def metrics(self, pid: int) -> dict[str, int | float]:
        """读取原生验收进程的资源计数，不引入生产诊断端点。"""
        value: ThawedJsonValue = json.loads(await self.exchange(f"metrics {pid}"))
        assert isinstance(value, dict)
        assert set(value) == {"handles", "threads", "privateBytes", "gdi", "user", "cpuMs"}
        result: dict[str, int | float] = {}
        for name, count in value.items():
            assert isinstance(count, (int, float)) and not isinstance(count, bool) and count >= 0
            result[name] = count
        return result


@pytest.fixture
async def controlled_window(repository_root: Path, native_executable: Path) -> typing.AsyncIterator[ControlledWindow]:
    """创建带受控命令通道的专用窗口，结束时通过 EOF 或终止回收。"""
    executable = repository_root / "tests/fixtures/cua_windows/bin/Release/net10.0-windows/Fixture.exe"
    assert executable.is_file()
    process = await asyncio.create_subprocess_exec(str(executable), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        assert process.stdout is not None
        assert (await asyncio.wait_for(process.stdout.readline(), 5)).strip() == b"ready"
        yield ControlledWindow(process)
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
            _, stderr = await asyncio.wait_for(process.communicate(), 5)
            assert process.returncode == 0 and not stderr
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def find_fixture(session: DesktopSidecarSession, fixture: ControlledWindow) -> DesktopWindow:
    """通过正式观察 adapter 定位专用窗口。"""
    listing = await session.list_windows()
    return next(window for window in listing.windows if window.pid == fixture.process.pid)


@pytest.mark.anyio
async def test_js_native_observation_and_reset(
    repository_root: Path, tmp_path: Path, controlled_window: ControlledWindow,
) -> None:
    factory = DesktopSidecarFactory(repository_root / "sidecars")
    sessions: list[DesktopSidecarSession] = []

    async def create_session() -> DesktopSidecarSession:
        session = await factory()
        assert isinstance(session, DesktopSidecarSession)
        sessions.append(session)
        return session

    async with computer_harness(tmp_path, create_session) as harness:
        outcome = await harness.execute(f"""
            var listing = await host.tool('computer', {{action:'list_windows'}});
            var windowRef = listing.data.windows.find(w => w.pid === {controlled_window.process.pid}).window;
            var observation = await host.tool('computer', {{action:'observe', window:windowRef, screenshot:true}});
            console.log(JSON.stringify(observation.data));
            await host.emitImage(observation);
            await host.tool('computer', {{action:'validate_observation', window:windowRef, observation:observation.data.observation}});
        """, "call_native_observe")
        assert outcome.result.ok, outcome.result.fields
        metadata = json.loads(outcome.result.fields["data"]["output"])
        assert metadata["tree"]["error"] is None
        assert any(node["name"] == "Increment counter" for node in metadata["tree"]["nodes"])
        attachment = outcome.result.fields["attachments"][0]
        with Image.open(io.BytesIO(base64.b64decode(attachment["data_url"].split(",", 1)[1]))) as screenshot:
            assert screenshot.size == (metadata["image"]["width"], metadata["image"]["height"])
            assert screenshot.width > 100 and screenshot.height > 100
        assert len(sessions) == 1
        assert (await harness.execute("await host.tool('computer', {action:'reset'});", "call_native_reset")).result.ok
        assert sessions[0]._process.returncode == 0
        stale = await harness.execute("await host.tool('computer', {action:'validate_observation', window:windowRef, observation:observation.data.observation});", "call_native_stale")
        assert not stale.result.ok
        assert len(sessions) == 2
    assert all(session._process.returncode == 0 for session in sessions)


@pytest.mark.anyio
async def test_observation_replacement_and_session_isolation(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    other = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        first = await session.observe(window.reference, screenshot=False)
        await session.validate_observation(window.reference, first.reference)
        second = await session.observe(window.reference, screenshot=False)
        with pytest.raises(DesktopError) as failure:
            await session.validate_observation(window.reference, first.reference)
        assert failure.value.code == "stale_observation"
        await session.validate_observation(window.reference, second.reference)
        with pytest.raises(DesktopError) as failure:
            await other.validate_observation(window.reference, second.reference)
        assert failure.value.code == "stale_observation"
        await session.validate_observation(window.reference, second.reference)
    finally:
        await asyncio.gather(session.close(), other.close())


@pytest.mark.anyio
@pytest.mark.parametrize(("command", "code"), [
    ("move", "window_changed"), ("resize", "window_changed"),
    ("hide", "window_unavailable"), ("minimize", "window_unavailable"),
])
async def test_window_changes_invalidate_observation(
    native_executable: Path, controlled_window: ControlledWindow, command: str, code: str,
) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        state = await session.observe(window.reference, screenshot=False)
        await controlled_window.command(command)
        with pytest.raises(DesktopError) as failure:
            await session.validate_observation(window.reference, state.reference)
        assert failure.value.code == code
        with pytest.raises(DesktopError) as failure:
            await session.validate_observation(window.reference, state.reference)
        assert failure.value.code == "stale_observation"
    finally:
        await session.close()


@pytest.mark.anyio
async def test_failed_refresh_discards_previous_observation(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        state = await session.observe(window.reference, screenshot=False)
        await controlled_window.command("hide")
        with pytest.raises(DesktopError) as failure:
            await session.observe(window.reference, screenshot=False)
        assert failure.value.code == "window_unavailable"
        await controlled_window.command("show")
        with pytest.raises(DesktopError) as failure:
            await session.validate_observation(window.reference, state.reference)
        assert failure.value.code == "stale_observation"
    finally:
        await session.close()


@pytest.mark.anyio
async def test_move_and_restore_does_not_revive_snapshot(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        state = await session.observe(window.reference, screenshot=False)
        await controlled_window.command("move")
        await controlled_window.command("restore")
        restored = await find_fixture(session, controlled_window)
        assert restored.bounds == state.bounds and restored.reference == window.reference
        with pytest.raises(DesktopError) as failure:
            await session.validate_observation(window.reference, state.reference)
        assert failure.value.code == "window_changed"
        fresh = await session.observe(window.reference, screenshot=False)
        await session.validate_observation(window.reference, fresh.reference)
    finally:
        await session.close()


@pytest.mark.anyio
async def test_same_process_window_recreation_rejects_old_references(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    try:
        for _ in range(20):
            window = await find_fixture(session, controlled_window)
            state = await session.observe(window.reference, screenshot=False)
            await controlled_window.command("recreate")
            with pytest.raises(DesktopError) as failure:
                await session.validate_observation(window.reference, state.reference)
            assert failure.value.code == "stale_window"
            renewed = await find_fixture(session, controlled_window)
            assert renewed.pid == window.pid and renewed.reference != window.reference
            with pytest.raises(DesktopError) as failure:
                await session.observe(window.reference, screenshot=False)
            assert failure.value.code == "stale_window"
    finally:
        await session.close()


@pytest.mark.anyio
async def test_observation_expires_on_monotonic_deadline(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        state = await session.observe(window.reference, screenshot=False)
        await session.validate_observation(window.reference, state.reference)
        await asyncio.sleep(12)
        await session.validate_observation(window.reference, state.reference)
        await asyncio.sleep(109)
        with pytest.raises(DesktopError) as failure:
            await session.validate_observation(window.reference, state.reference)
        assert failure.value.code == "stale_observation"
        fresh = await session.observe(window.reference, screenshot=False)
        await session.validate_observation(window.reference, fresh.reference)
    finally:
        await session.close()


@pytest.mark.anyio
async def test_repeated_capture_resources_stay_bounded(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        # 预热与采样执行相同链路，避免把线程池及 UIA 初始化误判为持续增长。
        for _ in range(32):
            state = await session.observe(window.reference, screenshot=True)
            await session.validate_observation(window.reference, state.reference)
        baseline = await controlled_window.metrics(session._process.pid)
        samples = []
        for _ in range(5):
            for _ in range(20):
                state = await session.observe(window.reference, screenshot=True)
                assert state.image is not None
                await session.validate_observation(window.reference, state.reference)
            samples.append(await controlled_window.metrics(session._process.pid))
        print(f"Native capture resource baseline={baseline}; batches={samples}")
        for sample in samples:
            assert sample["handles"] <= baseline["handles"] + 32
            assert sample["threads"] <= baseline["threads"] + 8
            assert sample["privateBytes"] <= baseline["privateBytes"] + 64 * 1024 * 1024
            assert sample["gdi"] <= baseline["gdi"] + 4
            assert sample["user"] <= baseline["user"] + 4
    finally:
        await session.close()
    assert session._process.returncode is not None


@pytest.mark.anyio
async def test_repeated_native_start_cancel_and_close(native_executable: Path, controlled_window: ControlledWindow) -> None:
    for index in range(12):
        session = await create_desktop_session(native_executable)
        try:
            window = await find_fixture(session, controlled_window)
            if index % 2:
                pending = asyncio.create_task(session.observe(window.reference, screenshot=True))
                await asyncio.sleep(0.005)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            else:
                state = await session.observe(window.reference, screenshot=False)
                await session.validate_observation(window.reference, state.reference)
        finally:
            await asyncio.gather(session.close(), session.close())
        assert session._process.returncode is not None
