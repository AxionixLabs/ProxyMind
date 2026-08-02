# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.subagents import delivery as delivery_module
from mind_app.runtime.subagents.delivery import (
    AgentActiveTurn,
    AgentMessageReceipt,
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
    def __init__(self, status="accepted") -> None:
        self.status = status
        self.calls = []

    async def deliver(self, context, turn_input):
        self.calls.append((context, turn_input))
        if self.status is None:
            return None
        return AgentMessageReceipt(
            status=self.status,
            turn_id=context.turn_id,
            client_message_id=turn_input.client_message_id,
        )


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
    receipt = await active.deliver(event)

    delivered_context, turn_input = port.calls[0]
    assert receipt == AgentMessageReceipt(
        status="accepted",
        turn_id=context.turn_id,
        client_message_id=event.event_id,
    )
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
async def test_active_turn_settles_duplicate_receipt_without_stream_event() -> None:
    context = _context()
    active = AgentActiveTurn(context, _Delivery("duplicate"))
    event = _message()
    active.handle_event(MarkerEvent(
        type="turn.start",
        turn_id=context.turn_id,
    ))

    receipt = await active.deliver(event)

    assert receipt is not None
    assert receipt.status == "duplicate"
    assert active.handle_event(TurnInputAcceptedEvent(
        type="turn.input.accepted",
        turn_id=context.turn_id,
        client_message_id=event.event_id,
    )) is None


@pytest.mark.anyio
async def test_active_turn_rejects_mismatched_delivery_receipt() -> None:
    class MismatchedDelivery:
        async def deliver(self, context, _turn_input):
            return AgentMessageReceipt(
                status="accepted",
                turn_id=context.turn_id,
                client_message_id="another_event",
            )

    context = _context()
    active = AgentActiveTurn(context, MismatchedDelivery())
    event = _message()
    active.handle_event(MarkerEvent(
        type="turn.start",
        turn_id=context.turn_id,
    ))

    with pytest.raises(ValueError, match="does not match delivery"):
        await active.deliver(event)

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
    ("status", "receipt_status"),
    [
        ("accepted", "accepted"),
        ("duplicate", "duplicate"),
        ("turn_not_active", None),
        ("turn_not_steerable", None),
        ("turn_mismatch", None),
    ],
)
async def test_steering_delivery_maps_remote_status(
    monkeypatch,
    status,
    receipt_status,
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

    receipt = await SteeringMessageDelivery().deliver(
        context,
        turn_input,
    )

    assert (receipt.status if receipt is not None else None) == receipt_status
    if receipt is not None:
        assert receipt.turn_id == context.turn_id
        assert receipt.client_message_id == event.event_id


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


@pytest.mark.anyio
async def test_steering_delivery_recovers_ambiguous_acceptance_as_duplicate(
    monkeypatch,
) -> None:
    calls = []

    async def request(**kwargs):
        calls.append(kwargs["turn_input"].client_message_id)
        if len(calls) == 1:
            raise TurnControlRequestError("response lost after acceptance")
        return TurnControlResponse(
            status="duplicate",
            turn_id=kwargs["turn_id"],
            client_message_id=kwargs["turn_input"].client_message_id,
        )

    monkeypatch.setattr(delivery_module, "steer_turn", request)
    event = _message()
    receipt = await SteeringMessageDelivery().deliver(
        _context(),
        _turn_input(event),
    )

    assert receipt is not None
    assert receipt.status == "duplicate"
    assert calls == [event.event_id, event.event_id]
