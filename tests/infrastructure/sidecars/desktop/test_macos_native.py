import asyncio
import base64
import hashlib
import io
import json
import os
import signal
import sys
import typing
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    patch,
)

import httpx
import pytest
from PIL import (
    Image,
    ImageChops,
)
from pydantic import BaseModel

from agent.ports.desktop import (
    DesktopClick,
    DesktopDrag,
    DesktopError,
    DesktopInput,
    DesktopKey,
    DesktopMove,
    DesktopPoint,
    DesktopScroll,
    DesktopText,
    DesktopWindow,
)
from infrastructure.sidecars.assets import desktop_runtime_files
from infrastructure.sidecars.desktop.install import DesktopRuntimeInstaller
from infrastructure.sidecars.desktop.protocol import (
    decode_capabilities,
    decode_response,
    encode_request,
)
from infrastructure.sidecars.desktop.session import (
    DesktopSidecarSession,
    create_desktop_session,
)
from protocol.schema.runtime_manifest import RuntimeManifest
from tests.fixtures.computer import computer_harness


@asynccontextmanager
async def native_process(executable: Path, *, guard: bool = False) -> typing.AsyncIterator[asyncio.subprocess.Process]:
    """测试拥有真实子进程，失败路径也关闭输入和回收管道。"""
    process = await asyncio.create_subprocess_exec(str(executable), *(["--release-input"] if guard else []),
        "--parent-pid", str(os.getpid()), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        yield process
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.communicate(), 4)
        except TimeoutError:
            process.kill()
            await process.communicate()


@pytest.mark.anyio
async def test_macos_protocol_strictness_unicode_and_eof(macos_executable: Path) -> None:
    hello = encode_request("中文🙂", "hello", {})
    invalid = b'{"v":1,"id":"invalid","action":"hello","parameters":{"x":1,"x":2}}\n'
    version = b'{"v":2,"id":"version","action":"hello","parameters":{}}\n'
    async with native_process(macos_executable) as process:
        stdout, stderr = await asyncio.wait_for(process.communicate(hello + hello + invalid + version), 10)
        lines = stdout.splitlines(keepends=True)
        assert process.returncode == 0 and len(lines) == 4, stderr.decode()
        capabilities = decode_capabilities(decode_response(lines[0], "中文🙂"))
        assert capabilities.platform == "macos" and capabilities.coordinate_space == "logical_screen_points"
        assert [json.loads(line)["error"]["code"] for line in lines[1:]] == ["duplicate_request", "invalid_request", "protocol_version"]
        diagnostics = [json.loads(line) for line in stderr.splitlines()]
        assert len(diagnostics) == 3
        assert all({"id", "action", "phase", "code"} <= item.keys() for item in diagnostics)


@pytest.mark.anyio
@pytest.mark.parametrize("frame", [b'{"v":1', b" " * 65536 + b"\n"])
async def test_macos_incomplete_and_oversized_frame_terminates(macos_executable: Path, frame: bytes) -> None:
    async with native_process(macos_executable) as process:
        stdout, stderr = await asyncio.wait_for(process.communicate(frame), 5)
        assert process.returncode == 1 and not stdout
        assert json.loads(stderr)["code"] in {"truncated_frame", "request_limit"}


@pytest.mark.anyio
async def test_macos_fragmented_unicode_and_exact_frame_limit(macos_executable: Path) -> None:
    first = encode_request("分片", "hello", {})
    last = encode_request("boundary", "hello", {})
    payload = first + last[:-1] + b" " * (65536 - len(last)) + b"\n"
    split = payload.index("分".encode()) + 1
    async with native_process(macos_executable) as process:
        assert process.stdin is not None
        process.stdin.write(payload[:split])
        await process.stdin.drain()
        await asyncio.sleep(0.02)
        stdout, stderr = await asyncio.wait_for(process.communicate(payload[split:]), 5)
        assert process.returncode == 0 and not stderr
        assert [json.loads(line)["id"] for line in stdout.splitlines()] == ["分片", "boundary"]


@pytest.mark.anyio
async def test_macos_release_guard_lease_and_eof(macos_executable: Path) -> None:
    async def command(process: asyncio.subprocess.Process, text: str) -> bytes:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(text.encode() + b"\n")
        await process.stdin.drain()
        return await asyncio.wait_for(process.stdout.readline(), 2)

    async with native_process(macos_executable, guard=True) as first, native_process(macos_executable, guard=True) as second:
        assert await command(first, "begin 1") == b"ok\n"
        assert await command(second, "begin 2") == b"busy\n"
        assert first.stdin is not None
        first.stdin.close()
        assert await asyncio.wait_for(first.wait(), 2) == 0
        assert await command(second, "begin 2") == b"ok\n"
        assert await command(second, "end") == b"ok\n"


@pytest.mark.anyio
async def test_macos_diagnostics_are_bounded(macos_executable: Path) -> None:
    async with native_process(macos_executable) as process:
        assert process.stdin is not None and process.stdout is not None
        for index in range(300):
            process.stdin.write(encode_request(str(index), "unknown", {}))
            await process.stdin.drain()
            response = await asyncio.wait_for(process.stdout.readline(), 5)
            assert json.loads(response)["error"]["code"] == "unsupported_action"
        process.stdin.close()
        _, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert len(stderr.splitlines()) == 256


@pytest.mark.anyio
async def test_macos_backpressure_terminates_without_a_stdout_reader(macos_executable: Path) -> None:
    read_fd, write_fd = os.pipe()
    try:
        process = await asyncio.create_subprocess_exec(str(macos_executable), "--parent-pid", str(os.getpid()),
            stdin=asyncio.subprocess.PIPE, stdout=write_fd, stderr=asyncio.subprocess.DEVNULL)
        os.close(write_fd)
        write_fd = -1
        try:
            assert process.stdin is not None
            try:
                for index in range(1500):
                    process.stdin.write(encode_request(f"{index:0128}", "hello", {}))
                    await process.stdin.drain()
                    await asyncio.sleep(0.001)
            except (BrokenPipeError, ConnectionResetError):
                pass
            assert await asyncio.wait_for(process.wait(), 8) == 1
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


@pytest.mark.anyio
async def test_macos_overlay_preview_exits_without_input(macos_executable: Path) -> None:
    process = await asyncio.create_subprocess_exec(str(macos_executable), "--preview", "1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0 and not stdout, stderr.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


@pytest.mark.anyio
async def test_macos_native_parent_exit_and_signal_cleanup(macos_executable: Path) -> None:
    # 中间宿主退出后留下 stdout 管道；EOF 证明原生子进程已退出并关闭描述符。
    script = ("import subprocess,sys; p=subprocess.Popen([sys.argv[1],'--parent-pid',str(__import__('os').getpid())],"
              "stdin=subprocess.PIPE); print(p.pid,flush=True); sys.stdin.readline()")
    parent = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(macos_executable),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        assert parent.stdout is not None
        child_pid = int(await asyncio.wait_for(parent.stdout.readline(), 5))
        parent.kill()
        await asyncio.wait_for(parent.communicate(), 5)
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if parent.returncode is None:
            parent.kill()
            await parent.communicate()
    async with native_process(macos_executable) as process:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(encode_request("ready", "hello", {}))
        await process.stdin.drain()
        await asyncio.wait_for(process.stdout.readline(), 5)
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.communicate(), 3)


@pytest.mark.anyio
async def test_macos_real_bundle_download_install_and_launch(macos_executable: Path, tmp_path: Path) -> None:
    release = macos_executable.parents[4]
    metadata = json.loads((release / "artifact.json").read_bytes())
    body = (release / metadata["filename"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == metadata["hash"]
    manifest = RuntimeManifest.model_validate({
        "host": "Mind", "service": "cua", "station": "darwin", "arch": "arm64", "channel": "stable",
        "version": metadata["version"], "build": "native", "published_at": "2026-11-06", "notes": "native",
        "package": {"filename": metadata["filename"], "size": len(body), "hash": metadata["hash"],
                    "updated_at": "2026-11-06", "url": "https://downloads.test/native.zip"},
    })
    client = httpx.AsyncClient
    with (patch("infrastructure.sidecars.desktop.install.fetch_manifest", new_callable=AsyncMock, return_value=manifest) as fetch,
          patch("infrastructure.update.runtime.httpx.AsyncClient", side_effect=lambda **kwargs: client(
              transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)), **kwargs))):
        installed = await DesktopRuntimeInstaller(tmp_path, platform="darwin").ensure()
        assert len(desktop_runtime_files(installed.parents[3], platform="darwin")) >= 3
        session = await create_desktop_session(installed, platform="darwin")
        try:
            assert session.capabilities.platform == "macos"
        finally:
            await session.close()
        fetch.return_value = None
        assert await DesktopRuntimeInstaller(tmp_path, platform="darwin").ensure() == installed


@pytest.mark.anyio
async def test_macos_fixture_observation_input_and_stale_identity(macos_executable: Path, repository_root: Path, tmp_path: Path) -> None:
    session = await create_desktop_session(macos_executable, platform="darwin")
    fixture: asyncio.subprocess.Process | None = None
    try:
        try:
            await session.list_windows()
        except DesktopError as error:
            if error.code == "accessibility_denied":
                pytest.skip("Grant Accessibility to the responsible host application and reset before native GUI acceptance")
            raise
        if not session.capabilities.capture_supported or not session.capabilities.input_supported:
            pytest.skip("Native GUI acceptance requires Screen Recording, Accessibility and Input Monitoring")
        executable = repository_root / ".cache/cua-macos-fixture/release/desktop-fixture"
        assert executable.is_file(), "Build tests/fixtures/cua_macos first"
        fixture = await asyncio.create_subprocess_exec(str(executable), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        assert fixture.stdout is not None and fixture.stdin is not None
        initial = json.loads(await asyncio.wait_for(fixture.stdout.readline(), 5))
        assert initial["ready"]
        window = None
        listing = await session.list_windows()
        for _ in range(30):
            listing = await session.list_windows()
            window = next((window for window in listing.windows if window.pid == fixture.pid), None)
            if window is not None:
                break
            await asyncio.sleep(0.1)
        assert window is not None, {"truncated": listing.truncated, "pids": [window.pid for window in listing.windows]}
        await session.activate(window.reference)
        await asyncio.sleep(0.2)
        state = await session.observe(window.reference, screenshot=True)
        assert state.tree_error is None and state.image is not None
        image = Image.open(io.BytesIO(state.image.content))
        image.load()
        image.save(tmp_path / "macos-fixture.png")
        assert image.size == (state.image.width, state.image.height)
        assert image.width >= 640 and image.height >= 380
        assert max(abs(a-b) for a, b in zip(image.convert("RGB").getpixel((40, image.height-40)), (22, 38, 65))) <= 3
        counter = next(control for control in state.controls if control.automation_id == "increment")
        result = await session.input(DesktopInput(window.reference, state.reference, DesktopClick(counter.index, "left", 1)))
        assert result.events_submitted >= 2
        with pytest.raises(DesktopError, match="Observation"):
            await session.validate_observation(window.reference, state.reference)
        state = await session.observe(window.reference, screenshot=False)
        entry = next(control for control in state.controls if control.automation_id == "entry")
        await session.input(DesktopInput(window.reference, state.reference, DesktopClick(entry.index, "left", 1)))
        state = await session.observe(window.reference, screenshot=False)
        await session.input(DesktopInput(window.reference, state.reference, DesktopKey(("WIN", "A"))))
        state = await session.observe(window.reference, screenshot=False)
        await session.input(DesktopInput(window.reference, state.reference, DesktopText("中文🙂 CUA")))
        fixture.stdin.write(b"state\n")
        await fixture.stdin.drain()
        result = json.loads(await asyncio.wait_for(fixture.stdout.readline(), 2))
        assert result["count"] == 1 and result["text"] == "中文🙂 CUA"
        assert result["heldKeys"] == [] and result["heldButtons"] == []
        state = await session.observe(window.reference, screenshot=False)
        fixture.stdin.write(b"move\n")
        await fixture.stdin.drain()
        await fixture.stdout.readline()
        with pytest.raises(DesktopError) as changed:
            await session.validate_observation(window.reference, state.reference)
        assert changed.value.code == "window_changed"
        fixture.stdin.write(b"close\n")
        await fixture.stdin.drain()
        await fixture.stdout.readline()
        with pytest.raises(DesktopError) as closed:
            await session.observe(window.reference, screenshot=False)
        assert closed.value.code == "stale_window"
    finally:
        await session.close()
        if fixture is not None:
            if fixture.stdin is not None:
                fixture.stdin.close()
            await asyncio.wait_for(fixture.communicate(), 5)


class FixtureInputEvent(BaseModel):
    type: int
    flags: int
    sourcePID: int


class FixtureState(BaseModel):
    ready: bool
    count: int
    text: str
    heldKeys: list[int]
    hardwareKeys: list[int]
    flags: int
    heldButtons: list[int]
    drags: int
    scrolls: int
    blockerClicks: int
    events: list[FixtureInputEvent]


class FixtureOverlay(BaseModel):
    id: int
    title: str
    visible: bool
    alpha: float
    x: float
    y: float
    width: float
    height: float


class OverlaySample(BaseModel):
    windows: list[FixtureOverlay]
    cursorX: float
    cursorY: float
    frontmostPID: int


@dataclass
class MacWindow:
    process: asyncio.subprocess.Process

    async def exchange(self, command: str) -> bytes:
        """等待专用测试窗口确认命令，不扩展生产 IPC。"""
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(command.encode() + b"\n")
        await self.process.stdin.drain()
        return await asyncio.wait_for(self.process.stdout.readline(), 5)

    async def state(self, command: str = "state") -> FixtureState:
        """读取夹具输入结果及系统按下状态。"""
        return FixtureState.model_validate_json(await self.exchange(command))

    async def overlays(self, pid: int) -> OverlaySample:
        """从 Window Server 读取指定进程的真实窗口和系统指针位置。"""
        return OverlaySample.model_validate_json(await self.exchange(f"overlays {pid}"))


@pytest.fixture
async def macos_session(macos_executable: Path) -> typing.AsyncIterator[DesktopSidecarSession]:
    """新建具备真实权限的会话，退出时回收原生输入资源。"""
    session = await create_desktop_session(macos_executable, platform="darwin")
    try:
        if not session.capabilities.input_supported or not session.capabilities.capture_supported:
            pytest.skip("Requires Accessibility, Screen Recording and Input Monitoring for the responsible host")
        yield session
    finally:
        await session.close()


@pytest.fixture
async def macos_window(macos_session: DesktopSidecarSession, repository_root: Path) -> typing.AsyncIterator[MacWindow]:
    """创建独立 AppKit 夹具，并在断言失败时同样回收进程。"""
    executable = repository_root / ".cache/cua-macos-fixture/release/desktop-fixture"
    assert executable.is_file(), "Build tests/fixtures/cua_macos first"
    process = await asyncio.create_subprocess_exec(str(executable), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        assert process.stdout is not None
        assert FixtureState.model_validate_json(await asyncio.wait_for(process.stdout.readline(), 5)).ready
        yield MacWindow(process)
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.communicate(), 5)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def find_macos_fixture(session: DesktopSidecarSession, fixture: MacWindow) -> DesktopWindow:
    """等待正式枚举端口识别夹具的主窗口。"""
    for _ in range(30):
        listing = await session.list_windows()
        window = next((item for item in listing.windows if item.pid == fixture.process.pid and item.title == "CUA macOS Fixture"), None)
        if window is not None:
            return window
        await asyncio.sleep(0.1)
    pytest.fail("Native window enumeration did not find the AppKit fixture")


@pytest.mark.anyio
async def test_macos_cursor_feedback_size_hotspot_reuse_and_cleanup(
    macos_session: DesktopSidecarSession, macos_window: MacWindow,
) -> None:
    session, fixture = macos_session, macos_window
    window = await find_macos_fixture(session, fixture)
    await session.activate(window.reference)
    sizes: list[float] = []
    identities: set[int] | None = None
    for size in ("small", "large", "small"):
        await fixture.state(size)
        window = await find_macos_fixture(session, fixture)
        state = await session.observe(window.reference, screenshot=True)
        assert state.image is not None
        cursor = await fixture.overlays(session._process.pid)
        fraction = 0.3 if cursor.cursorX > window.bounds.x + window.bounds.width * 0.5 else 0.7
        target = DesktopPoint(round(state.image.width * fraction), round(state.image.height * 0.7))
        active = asyncio.create_task(session.input(DesktopInput(window.reference, state.reference, DesktopMove(target, 1200))))
        widths, distances, positions = [], [], []
        try:
            while not active.done():
                sample = await fixture.overlays(session._process.pid)
                visible = [item for item in sample.windows if item.visible and item.alpha > 0.5]
                assert len(visible) <= 2
                if len(visible) == 2:
                    assert sample.frontmostPID == fixture.process.pid
                    current = {item.id for item in visible}
                    if identities is None:
                        identities = current
                    assert current == identities
                    pointer = min(visible, key=lambda item: item.width)
                    widths.append(pointer.width)
                    positions.append((pointer.x, pointer.y))
                    distances.append(max(abs(pointer.x + pointer.width / 2 - sample.cursorX),
                                         abs(pointer.y + pointer.height / 2 - sample.cursorY)))
                await asyncio.sleep(0.025)
            assert (await active).events_submitted > 0
        finally:
            if not active.done():
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
        assert len(set(positions)) >= 3
        assert widths and all(53 <= width <= 67 for width in widths)
        assert sorted(distances)[len(distances) // 2] < 12
        sizes.append(sorted(widths)[len(widths) // 2])
        for _ in range(25):
            idle = await fixture.overlays(session._process.pid)
            if not any(item.visible for item in idle.windows):
                break
            await asyncio.sleep(0.02)
        assert not any(item.visible for item in idle.windows), idle
    assert sizes[0] < sizes[1] and sizes[0] == sizes[2], sizes
    await session.close()
    assert not (await fixture.overlays(session._process.pid)).windows


@pytest.mark.anyio
async def test_macos_blocking_and_click_through_panels(
    macos_session: DesktopSidecarSession, macos_window: MacWindow,
) -> None:
    session, fixture = macos_session, macos_window
    window = await find_macos_fixture(session, fixture)
    await session.activate(window.reference)
    await fixture.state("block")
    state = await session.observe(window.reference, screenshot=False)
    button = next(control for control in state.controls if control.automation_id == "increment")
    with pytest.raises(DesktopError) as blocked:
        await session.input(DesktopInput(window.reference, state.reference, DesktopClick(button.index, "left", 1)))
    assert blocked.value.code == "target_obscured"
    result = await fixture.state()
    assert result.count == 0 and result.blockerClicks == 0
    await fixture.state("through")
    state = await session.observe(window.reference, screenshot=False)
    button = next(control for control in state.controls if control.automation_id == "increment")
    await session.input(DesktopInput(window.reference, state.reference, DesktopClick(button.index, "left", 1)))
    result = await fixture.state()
    assert result.count == 1 and result.blockerClicks == 0
    assert not result.heldButtons and not result.heldKeys


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["complete", "cancel", "eof", "kill", "interrupt"])
async def test_macos_drag_release_and_interruption(
    macos_session: DesktopSidecarSession, macos_window: MacWindow, ending: str,
) -> None:
    session, fixture = macos_session, macos_window
    window = await find_macos_fixture(session, fixture)
    await session.activate(window.reference)
    state = await session.observe(window.reference, screenshot=True)
    assert state.image is not None
    start = DesktopPoint(round(state.image.width * 0.2), round(state.image.height * 0.8))
    end = DesktopPoint(round(state.image.width * 0.6), start.y)
    active = asyncio.create_task(session.input(DesktopInput(window.reference, state.reference, DesktopDrag(start, end, 1200))))
    try:
        for _ in range(100):
            if (await fixture.state()).heldButtons:
                break
            if active.done():
                await active
                pytest.fail("Drag completed without a held mouse button sample")
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Drag never held its mouse button")
        if ending == "cancel":
            active.cancel()
            with pytest.raises(asyncio.CancelledError):
                await active
        elif ending in {"eof", "kill", "interrupt"}:
            if ending == "kill":
                session._process.kill()
            elif ending == "eof":
                assert session._process.stdin is not None
                session._process.stdin.close()
            else:
                await fixture.state("interrupt")
            with pytest.raises(DesktopError) as interrupted:
                await active
            if ending == "interrupt":
                assert interrupted.value.code == "user_interrupted"
                fresh = await session.observe(window.reference, screenshot=False)
                with pytest.raises(DesktopError) as stopped:
                    await session.input(DesktopInput(window.reference, fresh.reference, DesktopText("must not appear")))
                assert stopped.value.code == "input_stopped"
        else:
            assert (await active).events_submitted > 2
            result = await fixture.state()
            assert result.drags > 0
            assert not result.heldKeys and not result.heldButtons, result
            state = await session.observe(window.reference, screenshot=True)
            result = await fixture.state()
            assert not result.heldKeys and not result.heldButtons, result
            await session.input(DesktopInput(window.reference, state.reference, DesktopScroll(end, 1, 2)))
            assert (await fixture.state()).scrolls > 0
        for _ in range(100):
            result = await fixture.state()
            if not result.heldButtons and not result.heldKeys:
                break
            await asyncio.sleep(0.02)
        assert not result.heldButtons and not result.heldKeys
        await session.close()
        assert not (await fixture.overlays(session._process.pid)).windows
    finally:
        if not active.done():
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)


@pytest.mark.anyio
async def test_macos_native_js_observation_and_reset(
    macos_executable: Path, macos_window: MacWindow, tmp_path: Path,
) -> None:
    sessions: list[DesktopSidecarSession] = []

    async def factory() -> DesktopSidecarSession:
        """将真实 Mac 进程交给生产 Harness 管理。"""
        session = await create_desktop_session(macos_executable, platform="darwin")
        sessions.append(session)
        return session

    async with computer_harness(tmp_path, factory) as harness:
        outcome = await harness.execute(f"""
            var listing = await host.tool('computer', {{action:'list_windows'}});
            var windowRef = listing.data.windows.find(w => w.pid === {macos_window.process.pid}).window;
            var observation = await host.tool('computer', {{action:'observe', window:windowRef, screenshot:true}});
            console.log(JSON.stringify(observation.data));
            await host.emitImage(observation);
            await host.tool('computer', {{action:'validate_observation', window:windowRef, observation:observation.data.observation}});
        """, "call_macos_observe")
        assert outcome.result.ok, outcome.result.fields
        metadata = json.loads(outcome.result.fields["data"]["output"])
        assert metadata["coordinateSpace"] == "logical_screen_points"
        assert metadata["tree"]["error"] is None
        assert any(node["automationId"] == "increment" for node in metadata["tree"]["nodes"])
        attachment = outcome.result.fields["attachments"][0]
        with Image.open(io.BytesIO(base64.b64decode(attachment["data_url"].split(",", 1)[1]))) as screenshot:
            assert screenshot.size == (metadata["image"]["width"], metadata["image"]["height"])
        assert len(sessions) == 1
        assert (await harness.execute("await host.tool('computer', {action:'reset'});", "call_macos_reset")).result.ok
        assert sessions[0]._process.returncode == 0
        stale = await harness.execute(
            "await host.tool('computer', {action:'validate_observation', window:windowRef, observation:observation.data.observation});",
            "call_macos_stale")
        assert not stale.result.ok and len(sessions) == 2
    assert all(session._process.returncode == 0 for session in sessions)


@pytest.mark.anyio
async def test_macos_replaced_observation_and_failed_refresh_stay_invalid(
    macos_session: DesktopSidecarSession, macos_window: MacWindow, macos_executable: Path,
) -> None:
    session, fixture = macos_session, macos_window
    window = await find_macos_fixture(session, fixture)
    first = await session.observe(window.reference, screenshot=False)
    second = await session.observe(window.reference, screenshot=False)
    with pytest.raises(DesktopError) as replaced:
        await session.validate_observation(window.reference, first.reference)
    assert replaced.value.code == "stale_observation"
    await session.validate_observation(window.reference, second.reference)
    other = await create_desktop_session(macos_executable, platform="darwin")
    try:
        with pytest.raises(DesktopError) as foreign:
            await other.validate_observation(window.reference, second.reference)
        assert foreign.value.code == "stale_observation"
    finally:
        await other.close()
    await fixture.state("minimize")
    for _ in range(30):
        current = await find_macos_fixture(session, fixture)
        if current.minimized:
            break
        await asyncio.sleep(0.05)
    assert current.minimized
    with pytest.raises(DesktopError) as unavailable:
        await session.observe(current.reference, screenshot=False)
    assert unavailable.value.code == "window_unavailable"
    await fixture.state("restore")
    with pytest.raises(DesktopError) as stale:
        await session.validate_observation(window.reference, second.reference)
    assert stale.value.code == "stale_observation"


@pytest.mark.anyio
async def test_macos_repeated_capture_memory_stays_bounded(
    macos_session: DesktopSidecarSession, macos_window: MacWindow,
) -> None:
    session = macos_session
    window = await find_macos_fixture(session, macos_window)

    async def resident_bytes() -> int:
        """读取真实原生进程的驻留内存，不新增生产诊断入口。"""
        process = await asyncio.create_subprocess_exec("/bin/ps", "-o", "rss=", "-p", str(session._process.pid),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, stderr.decode()
        return int(stdout.strip()) * 1024

    for _ in range(8):
        await session.observe(window.reference, screenshot=True)
    baseline = await resident_bytes()
    samples = []
    for _ in range(4):
        for _ in range(10):
            state = await session.observe(window.reference, screenshot=True)
            assert state.image is not None
            await session.validate_observation(window.reference, state.reference)
        samples.append(await resident_bytes())
    assert max(samples) <= baseline + 64 * 1024**2, (baseline, samples)


@pytest.mark.anyio
async def test_macos_window_capture_excludes_visible_overlay(
    macos_session: DesktopSidecarSession, macos_window: MacWindow, macos_executable: Path, tmp_path: Path,
) -> None:
    session, fixture = macos_session, macos_window
    await fixture.state("large")
    window = await find_macos_fixture(session, fixture)
    await session.activate(window.reference)
    positioning = await session.observe(window.reference, screenshot=True)
    assert positioning.image is not None
    await session.input(DesktopInput(window.reference, positioning.reference, DesktopMove(
        DesktopPoint(round(positioning.image.width * 0.6), round(positioning.image.height * 0.7)), 120)))
    before = await session.observe(window.reference, screenshot=True)
    assert before.image is not None
    preview = await asyncio.create_subprocess_exec(str(macos_executable), "--preview", "5",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        for _ in range(30):
            sample = await fixture.overlays(preview.pid)
            visible = [item for item in sample.windows if item.visible and item.alpha > 0.9]
            if len(visible) == 2:
                break
            await asyncio.sleep(0.025)
        assert len(visible) == 2
        pointer = min(visible, key=lambda item: item.width)
        assert 53 <= pointer.width <= 67
        assert abs(pointer.x + pointer.width / 2 - sample.cursorX) < 2
        assert abs(pointer.y + pointer.height / 2 - sample.cursorY) < 2
        after = await session.observe(window.reference, screenshot=True)
        assert after.image is not None
        assert before.bounds == after.bounds
        later = await fixture.overlays(preview.pid)
        moved = next(item for item in later.windows if item.id == pointer.id)
        assert abs(moved.x + moved.width / 2 - later.cursorX) < 2
        assert abs(moved.y + moved.height / 2 - later.cursorY) < 2
        # 预览允许捕获；独立窗口过滤仍须排除与目标重叠的边缘柔光和鼠标光晕。
        scale_x, scale_y = before.image.width / before.bounds.width, before.image.height / before.bounds.height
        left = round((min(pointer.x, moved.x) - before.bounds.x - 10) * scale_x)
        top = round((min(pointer.y, moved.y) - before.bounds.y - 10) * scale_y)
        right = round((max(pointer.x + pointer.width, moved.x + moved.width) - before.bounds.x + 10) * scale_x)
        bottom = round((max(pointer.y + pointer.height, moved.y + moved.height) - before.bounds.y + 10) * scale_y)
        assert 0 <= left < right <= before.image.width and 0 <= top < bottom <= before.image.height
        with Image.open(io.BytesIO(before.image.content)) as first, Image.open(io.BytesIO(after.image.content)) as second:
            first.save(tmp_path / "before.png")
            second.save(tmp_path / "during.png")
            difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB")).crop((left, top, right, bottom))
            # SCK 转为 sRGB 时可能产生个位数舍入差；蓝色光晕的差异远高于此阈值。
            assert all(maximum <= 3 for _, maximum in difference.getextrema()), difference.getextrema()
    finally:
        if preview.returncode is None:
            preview.terminate()
        await asyncio.wait_for(preview.communicate(), 5)
