# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from infrastructure.errors import AppError
from infrastructure.services import helix_capability
from infrastructure.services.helix_capability import ServerManageHelixCapability
from infrastructure.services import runtime_owner
from infrastructure.services.runtime_owner import ServiceRuntimeOwner


@pytest.mark.anyio
async def test_service_runtime_owner_reuses_active_startup() -> None:
    owner = ServiceRuntimeOwner()
    operation_called = Mock()

    async def operation() -> bool:
        operation_called()
        await asyncio.sleep(0)
        return True

    results = await asyncio.gather(
        owner.run_startup(operation),
        owner.run_startup(operation),
    )

    assert results == [True, True]
    assert operation_called.call_count == 1

    assert await owner.run_startup(operation)
    assert operation_called.call_count == 2


@pytest.mark.anyio
async def test_service_runtime_owner_cancels_active_startup_and_can_retry() -> None:
    owner = ServiceRuntimeOwner()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_operation() -> bool:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    startup = asyncio.create_task(owner.run_startup(pending_operation))
    await started.wait()

    await owner.cancel_startup()

    with pytest.raises(asyncio.CancelledError):
        await startup
    assert cancelled.is_set()
    assert await owner.run_startup(lambda: _ready_result())


@pytest.mark.anyio
async def test_service_runtime_owner_stop_preserves_bound_manager(
    monkeypatch,
) -> None:
    terminated = []
    manager = SimpleNamespace(
        port=9123,
        close=AsyncMock(),
    )
    owner = ServiceRuntimeOwner()
    owner.bind(manager, SimpleNamespace())

    async def terminate(port: int) -> None:
        terminated.append(port)

    monkeypatch.setattr(runtime_owner, "terminate_port_process", terminate)

    await owner.stop()

    assert owner.manager is manager
    assert terminated == [9123]
    manager.close.assert_not_awaited()


@pytest.mark.anyio
async def test_service_runtime_owner_reboots_and_restores_keepalive(
    monkeypatch,
) -> None:
    keepalive_started = asyncio.Event()

    async def keepalive(stop_event, *, server_manager) -> None:
        assert server_manager is manager
        keepalive_started.set()
        await stop_event.wait()

    manager = SimpleNamespace(
        port=9123,
        restart=AsyncMock(),
        wait_until_ready=AsyncMock(return_value=True),
        close=AsyncMock(),
    )
    owner = ServiceRuntimeOwner()
    owner.bind(manager, SimpleNamespace())
    monkeypatch.setattr(runtime_owner, "run_keepalive", keepalive)

    await owner.reboot()
    await keepalive_started.wait()

    manager.restart.assert_awaited_once_with()
    manager.wait_until_ready.assert_awaited_once_with(10.0, 0.3)

    await owner.close()


@pytest.mark.anyio
async def test_service_runtime_owner_closes_keepalive_before_manager(
    monkeypatch,
) -> None:
    timeline = []
    keepalive_started = asyncio.Event()

    async def keepalive(stop_event, *, server_manager) -> None:
        assert server_manager is manager
        keepalive_started.set()
        try:
            await stop_event.wait()
        finally:
            timeline.append("keepalive")

    async def close_manager() -> None:
        timeline.append("manager")

    async def terminate(port: int) -> None:
        timeline.append(("terminate", port))

    manager = SimpleNamespace(
        port=9123,
        close=close_manager,
    )
    owner = ServiceRuntimeOwner()
    owner.bind(manager, SimpleNamespace())
    owner.request_termination_on_close()
    monkeypatch.setattr(runtime_owner, "run_keepalive", keepalive)
    monkeypatch.setattr(runtime_owner, "terminate_port_process", terminate)

    owner.start_keepalive()
    await keepalive_started.wait()
    await owner.close()

    assert timeline == ["keepalive", "manager", ("terminate", 9123)]
    assert owner.manager is None
    with pytest.raises(AppError, match="context is not bound"):
        owner.require_context()


@pytest.mark.anyio
async def test_server_manage_helix_capability_owns_lifecycle_port(monkeypatch) -> None:
    terminated: list[int] = []
    manager = SimpleNamespace(
        port=9123,
        ensure_running=AsyncMock(),
        restart=AsyncMock(),
        wait_until_ready=AsyncMock(return_value=True),
        close=AsyncMock(),
    )

    async def terminate(port: int) -> None:
        terminated.append(port)

    monkeypatch.setattr(helix_capability, "terminate_port_process", terminate)
    capability = ServerManageHelixCapability(manager)

    await capability.ensure_ready(wait_sec=2.0)
    assert capability.state == "ready"
    await capability.restart(wait_sec=2.0)
    await capability.stop()
    await capability.aclose()

    manager.ensure_running.assert_awaited_once_with(wait_sec=2.0)
    manager.restart.assert_awaited_once_with()
    manager.wait_until_ready.assert_awaited_once_with(2.0, 0.3)
    manager.close.assert_awaited_once_with()
    assert terminated == [9123]
    assert capability.state == "closed"


async def _ready_result() -> bool:
    return True
