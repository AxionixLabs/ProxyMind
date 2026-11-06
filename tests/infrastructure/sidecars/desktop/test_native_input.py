import asyncio
import ctypes
import json
from pathlib import Path

import pytest

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
)
from infrastructure.sidecars.desktop.session import create_desktop_session
from tests.infrastructure.sidecars.desktop.test_native_reliability import (
    ControlledWindow,
    controlled_window,
    find_fixture,
)


class _CursorPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _CursorInfo(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint), ("flags", ctypes.c_uint),
        ("cursor", ctypes.c_void_p), ("position", _CursorPoint),
    ]


def _cursor_visible() -> bool:
    """读取真实系统光标状态，验证代理指针不会与原生光标重叠。"""
    info = _CursorInfo()
    info.size = ctypes.sizeof(info)
    assert ctypes.windll.user32.GetCursorInfo(ctypes.byref(info))
    return bool(info.flags & 1)


@pytest.mark.anyio
async def test_real_click_unicode_keys_and_stale_observation(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        await controlled_window.command("activate")
        await session.activate(window.reference)
        observation = await session.observe(window.reference, screenshot=False)
        button = next(control for control in observation.controls if control.automation_id == "increment")
        command = DesktopInput(window.reference, observation.reference, DesktopClick(button.index, "left", 1))
        result = await session.input(command)
        assert result.events_submitted > 2 and result.requires_observation
        state = json.loads(await controlled_window.exchange("state"))
        assert state["count"] == 1 and state["foreground"] and not state["held"]
        with pytest.raises(DesktopError, match="expired|replaced"):
            await session.input(command)
        observation = await session.observe(window.reference, screenshot=False)
        edit = next(control for control in observation.controls if control.automation_id == "textInput")
        await session.input(DesktopInput(window.reference, observation.reference, DesktopClick(edit.index, "left", 1)))
        observation = await session.observe(window.reference, screenshot=False)
        await session.input(DesktopInput(window.reference, observation.reference, DesktopKey(("CTRL", "A"))))
        observation = await session.observe(window.reference, screenshot=False)
        text = "CUA 中文 🐱" * 10
        cursor_before_text = _cursor_visible()
        active = asyncio.create_task(session.input(
            DesktopInput(window.reference, observation.reference, DesktopText(text))))
        hidden = False
        for _ in range(100):
            if not _cursor_visible():
                hidden = True
                break
            if active.done():
                break
            await asyncio.sleep(0.01)
        assert hidden
        await active
        await asyncio.sleep(0.1)
        state = json.loads(await controlled_window.exchange("state"))
        assert state["text"] == text and not state["held"]
        assert _cursor_visible() == cursor_before_text
    finally:
        await session.close()
    assert session._process.returncode == 0, session.stderr_tail


@pytest.mark.anyio
async def test_cross_process_exclusion_and_crash_release(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    other = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        other_window = await find_fixture(other, controlled_window)
        await controlled_window.command("activate")
        await session.activate(window.reference)
        observation = await session.observe(window.reference, screenshot=True)
        other_observation = await other.observe(other_window.reference, screenshot=True)
        drag = DesktopDrag(DesktopPoint(280, 300), DesktopPoint(400, 300), 1200)
        active = asyncio.create_task(session.input(DesktopInput(window.reference, observation.reference, drag)))
        for _ in range(100):
            if json.loads(await controlled_window.exchange("state"))["held"]:
                break
            await asyncio.sleep(0.01)
        else:
            await active
            pytest.fail("Drag never held its mouse button")
        with pytest.raises(DesktopError) as failure:
            await other.input(DesktopInput(other_window.reference, other_observation.reference,
                                           DesktopMove(DesktopPoint(300, 300), 0)))
        assert failure.value.code == "desktop_busy"
        session._process.kill()
        with pytest.raises(DesktopError) as failure:
            await active
        assert failure.value.code == "transport_closed"
        for _ in range(100):
            if not json.loads(await controlled_window.exchange("state"))["held"]:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Mouse button remained held after native process death")
        for _ in range(100):
            if _cursor_visible():
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("System cursor manager did not restore the cursor after native process death")
    finally:
        await asyncio.gather(session.close(), other.close())
    assert session._process.returncode != 0
    assert other._process.returncode == 0, other.stderr_tail


@pytest.mark.anyio
async def test_overlay_capture_exclusion_and_external_stop(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        cursor_before = _cursor_visible()
        await controlled_window.command("activate")
        await session.activate(window.reference)
        observation = await session.observe(window.reference, screenshot=True)
        active = asyncio.create_task(session.input(DesktopInput(window.reference, observation.reference,
            DesktopDrag(DesktopPoint(280, 300), DesktopPoint(400, 300), 1200))))
        for _ in range(100):
            if json.loads(await controlled_window.exchange("state"))["held"]:
                break
            await asyncio.sleep(0.01)
        else:
            await active
            pytest.fail("Drag never held its mouse button")
        assert not _cursor_visible()
        visuals = json.loads(await controlled_window.exchange(f"overlays {session._process.pid}"))
        assert len(visuals["overlays"]) == 2
        assert all(item["affinity"] == 0x11 and item["transparent"] for item in visuals["overlays"])
        assert visuals["pixel"] == [22, 38, 65]
        await controlled_window.command("interrupt")
        with pytest.raises(DesktopError) as failure:
            await active
        assert failure.value.code == "input_interrupted" and "user_interrupted" in failure.value.detail
        assert not json.loads(await controlled_window.exchange("state"))["held"]
        assert not json.loads(await controlled_window.exchange(f"overlays {session._process.pid}"))["overlays"]
        assert _cursor_visible() == cursor_before
        observation = await session.observe(window.reference, screenshot=False)
        with pytest.raises(DesktopError) as failure:
            await session.input(DesktopInput(window.reference, observation.reference, DesktopKey(("TAB",))))
        assert failure.value.code == "input_stopped"
    finally:
        await session.close()


@pytest.mark.anyio
async def test_window_change_updates_glow_then_stops_input(
    native_executable: Path, controlled_window: ControlledWindow,
) -> None:
    """窗口移动时先同步柔光边界，再拒绝继续使用旧观察。"""
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        await controlled_window.command("activate")
        await session.activate(window.reference)
        observation = await session.observe(window.reference, screenshot=True)
        active = asyncio.create_task(session.input(DesktopInput(window.reference, observation.reference,
            DesktopDrag(DesktopPoint(280, 300), DesktopPoint(400, 300), 1200))))
        for _ in range(100):
            if json.loads(await controlled_window.exchange("state"))["held"]:
                break
            await asyncio.sleep(0.01)
        else:
            await active
            pytest.fail("Drag never held its mouse button")
        before = json.loads(await controlled_window.exchange(f"overlays {session._process.pid}"))["overlays"]
        assert len(before) == 2
        await controlled_window.command("move")
        with pytest.raises(DesktopError) as failure:
            await active
        assert failure.value.code == "input_interrupted" and "window_changed" in failure.value.detail
        assert not json.loads(await controlled_window.exchange("state"))["held"]
        assert not json.loads(await controlled_window.exchange(f"overlays {session._process.pid}"))["overlays"]
    finally:
        await session.close()


@pytest.mark.anyio
async def test_real_coordinate_click_drag_scroll_and_cancel(native_executable: Path, controlled_window: ControlledWindow) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        await controlled_window.command("activate")
        await session.activate(window.reference)
        observation = await session.observe(window.reference, screenshot=True)
        button = next(control for control in observation.controls if control.automation_id == "increment")
        assert button.bounds is not None and observation.image is not None
        scale_x = observation.image.width / observation.bounds.width
        scale_y = observation.image.height / observation.bounds.height
        point = DesktopPoint(round((button.bounds.x + button.bounds.width / 2) * scale_x),
                             round((button.bounds.y + button.bounds.height / 2) * scale_y))
        await session.input(DesktopInput(window.reference, observation.reference, DesktopClick(point, "left", 1)))
        assert json.loads(await controlled_window.exchange("state"))["count"] == 1
        observation = await session.observe(window.reference, screenshot=True)
        await session.input(DesktopInput(window.reference, observation.reference,
                                        DesktopScroll(DesktopPoint(500, 290), 0, -2)))
        assert json.loads(await controlled_window.exchange("state"))["wheel"] == -240
        observation = await session.observe(window.reference, screenshot=True)
        edit = next(control for control in observation.controls if control.automation_id == "textInput")
        assert edit.bounds is not None
        start = DesktopPoint(round((edit.bounds.x + 5) * scale_x), round((edit.bounds.y + 10) * scale_y))
        end = DesktopPoint(start.x + 90, start.y)
        await session.input(DesktopInput(window.reference, observation.reference, DesktopDrag(start, end, 400)))
        state = json.loads(await controlled_window.exchange("state"))
        assert state["selection"] > 0 and not state["held"]
        observation = await session.observe(window.reference, screenshot=True)
        active = asyncio.create_task(session.input(DesktopInput(window.reference, observation.reference, DesktopDrag(start, end, 1200))))
        for _ in range(100):
            if json.loads(await controlled_window.exchange("state"))["held"]:
                break
            await asyncio.sleep(0.01)
        else:
            await active
            pytest.fail("Drag never held its mouse button")
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert not json.loads(await controlled_window.exchange("state"))["held"]
        assert session._process.returncode == 0, session.stderr_tail
    finally:
        await session.close()


@pytest.mark.anyio
async def test_pointer_tracks_input_and_reuses_hidden_windows(
    native_executable: Path, controlled_window: ControlledWindow,
) -> None:
    session = await create_desktop_session(native_executable)
    try:
        window = await find_fixture(session, controlled_window)
        await controlled_window.command("activate")
        await session.activate(window.reference)
        idle = json.loads(await controlled_window.exchange(f"overlays {session._process.pid}"))
        handles = set(idle["allocated"])
        assert len(handles) == 2 and not idle["overlays"]
        for x in (400, 280, 400):
            observation = await session.observe(window.reference, screenshot=True)
            active = asyncio.create_task(session.input(DesktopInput(window.reference, observation.reference,
                DesktopMove(DesktopPoint(x, 300), 1200))))
            positions = []
            distances = []
            try:
                while not active.done():
                    state = json.loads(await controlled_window.exchange(f"overlays {session._process.pid}"))
                    assert set(state["allocated"]) == handles
                    assert len(state["overlays"]) <= 2
                    if len(state["overlays"]) == 2:
                        pointer = min(state["overlays"], key=lambda item: item["width"])
                        assert pointer["affinity"] == 0x11 and pointer["transparent"]
                        positions.append((pointer["x"], pointer["y"]))
                        # 热点距小窗口左上角约三分之一宽度，允许一个渲染帧内的位置差。
                        hotspot = pointer["width"] / 3
                        distances.append(max(abs(pointer["x"] + hotspot - state["cursor"]["x"]),
                                             abs(pointer["y"] + hotspot - state["cursor"]["y"])))
                    await asyncio.sleep(0.04)
                await active
            finally:
                if not active.done():
                    active.cancel()
                    await asyncio.gather(active, return_exceptions=True)
            assert len(set(positions)) >= 3
            assert sorted(distances)[len(distances) // 2] < 32
            idle = json.loads(await controlled_window.exchange(f"overlays {session._process.pid}"))
            assert set(idle["allocated"]) == handles and not idle["overlays"]
    finally:
        await session.close()
    closed = json.loads(await controlled_window.exchange(f"overlays {session._process.pid}"))
    assert not closed["allocated"] and not closed["overlays"]
