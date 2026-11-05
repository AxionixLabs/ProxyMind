import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from agent.domain.policies import preset_permissions
from agent.harness.sessions.desktop import DesktopRuntime
from agent.ports.desktop import (
    DesktopError,
    DesktopScope,
    DesktopSessionPort,
    DesktopWindows,
    ListDesktopWindows,
)


def scope(session: str = "root") -> DesktopScope:
    return DesktopScope(session, "/workspace", preset_permissions("auto"))


def native() -> AsyncMock:
    session = AsyncMock(spec=DesktopSessionPort)
    session.list_windows.return_value = DesktopWindows((), False)
    return session


@pytest.mark.anyio
async def test_lazy_reuse_scope_change_and_reset() -> None:
    sessions = [native() for _ in range(4)]
    factory = AsyncMock(side_effect=sessions)
    runtime = DesktopRuntime(factory)
    factory.assert_not_called()
    try:
        await runtime.execute(scope(), ListDesktopWindows())
        await runtime.execute(scope(), ListDesktopWindows())
        assert factory.await_count == 1
        changed = replace(scope(), cwd="/other")
        await runtime.execute(changed, ListDesktopWindows())
        sessions[0].close.assert_awaited_once()
        changed = replace(changed, permissions=preset_permissions("full-access"))
        await runtime.execute(changed, ListDesktopWindows())
        sessions[1].close.assert_awaited_once()
        await runtime.reset_session("root")
        sessions[2].close.assert_awaited_once()
        await runtime.execute(changed, ListDesktopWindows())
        assert factory.await_count == 4
    finally:
        await runtime.close()
    sessions[3].close.assert_awaited_once()


@pytest.mark.anyio
async def test_cross_session_fifo_and_cancelled_queue_does_not_start() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    first, second = native(), native()

    async def blocked() -> DesktopWindows:
        entered.set()
        await release.wait()
        return DesktopWindows((), False)

    first.list_windows.side_effect = blocked
    factory = AsyncMock(side_effect=[first, second])
    runtime = DesktopRuntime(factory)
    running = asyncio.create_task(runtime.execute(scope(), ListDesktopWindows()))
    await entered.wait()
    cancelled = asyncio.create_task(runtime.execute(scope("cancelled"), ListDesktopWindows()))
    queued = asyncio.create_task(runtime.execute(scope("child"), ListDesktopWindows()))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert factory.await_count == 1
    release.set()
    await asyncio.gather(running, queued)
    assert factory.await_count == 2
    await runtime.close()
    assert not runtime._entries


@pytest.mark.anyio
async def test_close_cancels_active_and_rejects_old_queued_requests() -> None:
    entered = asyncio.Event()
    session = native()

    async def blocked() -> DesktopWindows:
        entered.set()
        await asyncio.Future()
        return DesktopWindows((), False)

    session.list_windows.side_effect = blocked
    factory = AsyncMock(return_value=session)
    runtime = DesktopRuntime(factory)
    running = asyncio.create_task(runtime.execute(scope(), ListDesktopWindows()))
    await entered.wait()
    queued = asyncio.create_task(runtime.execute(scope(), ListDesktopWindows()))
    await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(runtime.close_session("root"), runtime.close_session("root")), 2)
    with pytest.raises(asyncio.CancelledError):
        await running
    with pytest.raises(DesktopError, match="closed while queued"):
        await queued
    session.close.assert_awaited_once()
    await runtime.close()
    with pytest.raises(DesktopError, match="runtime is closed"):
        await runtime.execute(scope(), ListDesktopWindows())
    assert factory.await_count == 1


@pytest.mark.anyio
async def test_cancelled_close_waiter_does_not_abandon_resources() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    session = native()

    async def close() -> None:
        entered.set()
        await release.wait()

    session.close.side_effect = close
    runtime = DesktopRuntime(AsyncMock(return_value=session))
    await runtime.execute(scope(), ListDesktopWindows())
    closing = asyncio.create_task(runtime.close())
    await entered.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    release.set()
    await asyncio.wait_for(runtime.close(), 2)
    assert not runtime._entries
    session.close.assert_awaited_once()


@pytest.mark.anyio
async def test_close_failure_retains_ownership_and_retries() -> None:
    session = native()
    session.close.side_effect = [OSError("close failed"), None]
    runtime = DesktopRuntime(AsyncMock(return_value=session))
    await runtime.execute(scope(), ListDesktopWindows())
    with pytest.raises(ExceptionGroup):
        await runtime.close()
    assert "root" in runtime._entries
    await runtime.close()
    assert not runtime._entries


@pytest.mark.anyio
@pytest.mark.parametrize("code", ["stale_observation", "protocol_error"])
async def test_failed_requests_are_not_replayed(code: str) -> None:
    session = native()
    session.list_windows.side_effect = DesktopError(code, "failed")
    factory = AsyncMock(return_value=session)
    runtime = DesktopRuntime(factory)
    with pytest.raises(DesktopError):
        await runtime.execute(scope(), ListDesktopWindows())
    assert session.list_windows.await_count == 1 and factory.await_count == 1
    assert session.close.await_count == (code == "protocol_error")
    await runtime.close()
