# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_app.approval.coordinator import ApprovalCoordinator


@pytest.mark.anyio
async def test_approval_coordinator_serializes_concurrent_requests() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = []

    class Interaction:
        approval_source = "user"

        async def request_approval(self, approval):
            calls.append(approval["id"])
            if approval["id"] == "first":
                first_started.set()
                await release_first.wait()
            return "accept"

    coordinator = ApprovalCoordinator(Interaction())
    first = asyncio.create_task(coordinator.request({"id": "first"}))
    await first_started.wait()
    second = asyncio.create_task(coordinator.request({"id": "second"}))
    await asyncio.sleep(0)

    assert calls == ["first"]

    release_first.set()
    assert await asyncio.gather(first, second) == ["accept", "accept"]
    assert calls == ["first", "second"]


@pytest.mark.anyio
async def test_approval_cancellation_releases_next_request() -> None:
    started = asyncio.Event()

    class Interaction:
        approval_source = "user"

        async def request_approval(self, approval):
            if approval["id"] == "first":
                started.set()
                await asyncio.Event().wait()
            return "decline"

    coordinator = ApprovalCoordinator(Interaction())
    first = asyncio.create_task(coordinator.request({"id": "first"}))
    await started.wait()
    second = asyncio.create_task(coordinator.request({"id": "second"}))

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert await asyncio.wait_for(second, timeout=1) == "decline"


def test_approval_coordinator_exposes_interaction_decision_source() -> None:
    coordinator = ApprovalCoordinator(type("Interaction", (), {
        "approval_source": "policy",
    })())

    assert coordinator.decision_source == "policy"
