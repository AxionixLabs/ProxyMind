# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.subagents import delivery as delivery_module
from mind_app.runtime.subagents.delivery import (
    AgentActiveTurn,
    SteeringMessageDelivery,
)
from mind_app.runtime.subagents.mailbox import AgentMailboxStore
from mind_core.permissions import preset_permissions
from mind_nova.requests.turn_control import (
    TurnControlRequestError,
    TurnControlResponse,
)
from mind_nova.stream_events import (
    MarkerEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
)
from mind_nova.turn_inputs import TurnInput


class _Delivery:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls = []

    async def deliver(self, context, turn_input) -> bool:
        self.calls.append((context, turn_input))
        return self.accepted


def _context() -> TurnContext:
    root = AgentContext.root("sid_root")
    worker = root.child("worker", "worker", agent_id="agent_worker")
    return TurnContext.create(
        agent=worker,
        cid="cid_test",
        sid="sid_worker",
        source="subagent",
        pref_config={},
        cwd="/workspace",
        permissions=preset_permissions("auto"),
        turn_id="turn_worker",
    )


def _message():
    context = _context()
    root = AgentContext.root(context.agent.root_session_id)
    mailbox = AgentMailboxStore()
    return mailbox.publish(
        "message",
        root,
        recipient=context.agent,
        message="avoid the database layer",
    )


def _turn_input(event) -> TurnInput:
    return TurnInput(
        client_message_id=event.event_id,
        text=event.message,
    )


@pytest.mark.anyio
async def test_active_turn_delivers_and_correlates_accepted_input() -> None:
    context = _context()
    port = _Delivery()
    active = AgentActiveTurn(context, port)
    event = _message()

    active.handle_event(MarkerEvent(
        type="turn.start",
        turn_id=context.turn_id,
    ))
    assert await active.deliver(event)

    delivered_context, turn_input = port.calls[0]
    assert delivered_context is context
    assert turn_input.client_message_id == event.event_id
    assert turn_input.text == event.message
    assert turn_input.extras["agent_mailbox"] == {
        "event_id": event.event_id,
        "source_agent_id": "root",
        "source_task_path": "/root",
        "recipient_agent_id": context.agent.agent_id,
        "recipient_task_path": context.agent.task_path,
    }
    assert active.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id=context.turn_id,
        client_message_id=event.event_id,
    )) is turn_input
    assert active.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id=context.turn_id,
        client_message_id=event.event_id,
    )) is None


@pytest.mark.anyio
async def test_active_turn_refuses_unready_settled_and_closed_delivery() -> None:
    context = _context()
    port = _Delivery()
    event = _message()
    unready = AgentActiveTurn(context, port, ready_timeout_sec=0.01)

    assert not await unready.deliver(event)

    settled = AgentActiveTurn(context, port)
    settled.handle_event(MarkerEvent(
        type="turn.start",
        turn_id=context.turn_id,
    ))
    settled.handle_event(TurnLogicalSettledEvent(
        type="turn.logical_settled",
        turn_id=context.turn_id,
    ))
    assert not await settled.deliver(event)

    waiting = AgentActiveTurn(context, port)
    task = asyncio.create_task(waiting.deliver(event))
    await asyncio.sleep(0)
    waiting.close()
    assert not await task
    assert port.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "accepted"),
    [
        ("accepted", True),
        ("duplicate", True),
        ("turn_not_active", False),
        ("turn_not_steerable", False),
        ("turn_mismatch", False),
    ],
)
async def test_steering_delivery_maps_remote_status(
    monkeypatch,
    status,
    accepted,
) -> None:
    async def request(**kwargs):
        return TurnControlResponse(
            status=status,
            turn_id=kwargs["turn_id"],
            client_message_id=kwargs["turn_input"].client_message_id,
        )

    monkeypatch.setattr(delivery_module, "steer_turn", request)
    event = _message()
    context = _context()
    turn_input = _turn_input(event)

    assert await SteeringMessageDelivery().deliver(
        context,
        turn_input,
    ) is accepted


@pytest.mark.anyio
async def test_steering_delivery_retries_request_errors(monkeypatch) -> None:
    calls = []

    async def fail(**_kwargs):
        calls.append(True)
        raise TurnControlRequestError("offline")

    monkeypatch.setattr(delivery_module, "steer_turn", fail)
    event = _message()

    assert not await SteeringMessageDelivery().deliver(
        _context(),
        _turn_input(event),
    )
    assert len(calls) == 2
