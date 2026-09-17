# -*- coding: utf-8 -*-

import asyncio

import pytest

from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.domain.mcp_elicitation import (
    ElicitationRequest,
    ElicitationResponse,
    McpInvocation,
)
from frontends.interaction.noninteractive import NonInteractiveInteraction
from tests.agent.application.approvals.test_approval_coordinator import ControlledInteraction


class InputInteraction(ControlledInteraction):
    async def present_elicitation(self, request):
        self.calls.append(request.request_id)
        self.started.setdefault(request.request_id, asyncio.Event()).set()
        future = asyncio.get_running_loop().create_future()
        self.decisions[request.request_id] = future
        return await future


def request(name):
    return ElicitationRequest(name, "fixture", McpInvocation("s", "t", "c", "root"), "Ordinary information", fields=())


@pytest.mark.anyio
async def test_elicitation_shares_fifo_surface_without_approval_resolution_or_batch_cancel():
    interaction = InputInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request({"id": "approval"}))
    await interaction.wait_started("approval")
    form = asyncio.create_task(coordinator.request_elicitation(request("form")))
    second = asyncio.create_task(coordinator.request_elicitation(request("second")))
    await asyncio.sleep(0)
    interaction.finish("approval", "accept")
    assert await first == "accept"
    await interaction.wait_started("form")
    assert not await coordinator.resolve("form", "accept")
    interaction.decisions["form"].set_result(ElicitationResponse("cancel"))
    assert (await form).action == "cancel"
    await interaction.wait_started("second")
    interaction.decisions["second"].set_result(ElicitationResponse("accept"))
    assert (await second).action == "accept"
    await coordinator.close()
    assert interaction.calls == ["approval", "form", "second"]
    assert interaction.session_started == interaction.session_ended
    assert coordinator.snapshot.unresolved_count == 0


@pytest.mark.anyio
async def test_elicitation_caller_cancel_and_close_remove_only_owned_presentations():
    interaction = InputInteraction()
    coordinator = ApprovalCoordinator(interaction)
    first = asyncio.create_task(coordinator.request_elicitation(request("first")))
    await interaction.wait_started("first")
    second = asyncio.create_task(coordinator.request_elicitation(request("second")))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await interaction.wait_started("second")
    assert interaction.decisions["first"].cancelled()
    await coordinator.close()
    assert (await second).action == "cancel"
    assert interaction.decisions["second"].cancelled()


@pytest.mark.anyio
async def test_noninteractive_elicitation_declines_without_starting_a_queue():
    coordinator = ApprovalCoordinator(NonInteractiveInteraction())
    assert not coordinator.elicitation_supported
    assert (await coordinator.request_elicitation(request("headless"))).action == "decline"
    assert coordinator.snapshot.unresolved_count == 0
