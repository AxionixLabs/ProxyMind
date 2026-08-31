# -*- coding: utf-8 -*-

import pytest

from agent.application.execution import AgentContext
from agent.stores.agent_mailbox import (
    AgentMailboxStore,
    MAX_MAILBOX_CONTEXT_CHARS,
    format_mailbox_context,
)


def test_mailbox_tracks_unread_events_per_reader() -> None:
    root = AgentContext.root("sid_root")
    worker = root.child("worker", "worker", agent_id="agent_worker")
    reviewer = root.child("reviewer", "reviewer", agent_id="agent_reviewer")
    mailbox = AgentMailboxStore()
    event = mailbox.publish(
        "status",
        worker,
        status="completed",
        submission_id="submission_one",
    )

    assert mailbox.take_updates(root.agent_id, {worker.agent_id}) == (event,)
    assert mailbox.take_updates(root.agent_id, {worker.agent_id}) == ()
    assert mailbox.take_updates(reviewer.agent_id, {worker.agent_id}) == (event,)


def test_mailbox_messages_are_private_and_context_is_bounded() -> None:
    root = AgentContext.root("sid_root")
    worker = root.child("worker", "worker", agent_id="agent_worker")
    mailbox = AgentMailboxStore()
    message = "\\" * 7000
    event = mailbox.publish(
        "message",
        root,
        recipient=worker,
        message=message,
    )

    assert mailbox.take_updates(root.agent_id, {root.agent_id}) == ()
    received = mailbox.take_messages(worker.agent_id)
    context = format_mailbox_context(received)

    assert received == (event,)
    assert message in context
    assert len(context) <= MAX_MAILBOX_CONTEXT_CHARS

    with pytest.raises(ValueError, match="agent message exceeds"):
        mailbox.publish(
            "message",
            root,
            recipient=worker,
            message="\0" * 3000,
        )


def test_mailbox_capacity_discards_oldest_events() -> None:
    root = AgentContext.root("sid_root")
    worker = root.child("worker", "worker", agent_id="agent_worker")
    mailbox = AgentMailboxStore(capacity=2)

    for index in range(3):
        mailbox.publish(
            "status",
            worker,
            status="completed",
            submission_id=f"submission_{index}",
        )

    updates = mailbox.take_updates(root.agent_id, {worker.agent_id})
    assert [event.submission_id for event in updates] == [
        "submission_1",
        "submission_2",
    ]
    assert [event.sequence for event in updates] == [2, 3]


def test_mailbox_claim_acknowledges_only_matching_recipient_message() -> None:
    root = AgentContext.root("sid_root")
    worker = root.child("worker", "worker", agent_id="agent_worker")
    reviewer = root.child("reviewer", "reviewer", agent_id="agent_reviewer")
    mailbox = AgentMailboxStore()
    event = mailbox.publish(
        "message",
        root,
        recipient=worker,
        message="new constraint",
    )

    assert not mailbox.claim_message(
        reviewer.agent_id,
        event.event_id,
        "turn_reviewer",
    )
    assert mailbox.claim_message(
        worker.agent_id,
        event.event_id,
        "turn_worker",
    )
    assert mailbox.acknowledge_claim(
        worker.agent_id,
        "turn_worker",
        (event.event_id,),
    ) == (event.event_id,)
    assert mailbox.take_messages(worker.agent_id) == ()


def test_mailbox_claim_hides_message_until_release_and_is_not_persisted() -> None:
    root = AgentContext.root("sid_root")
    worker = root.child("worker", "worker", agent_id="agent_worker")
    mailbox = AgentMailboxStore()
    event = mailbox.publish(
        "message",
        root,
        recipient=worker,
        message="temporary constraint",
    )

    assert mailbox.claim_messages(
        worker.agent_id,
        "submission_one",
    ) == (event,)
    assert mailbox.take_messages(worker.agent_id) == ()
    assert mailbox.take_updates(worker.agent_id, {root.agent_id}) == ()

    restored = AgentMailboxStore.from_snapshot(mailbox.snapshot())
    assert restored.take_messages(worker.agent_id) == (event,)

    assert mailbox.release_claim(
        worker.agent_id,
        "submission_one",
        (event.event_id,),
    ) == (event.event_id,)
    assert mailbox.take_messages(worker.agent_id) == (event,)


def test_mailbox_snapshot_restores_events_consumption_and_sequence() -> None:
    root = AgentContext.root("sid_root")
    worker = root.child("worker", "worker", agent_id="agent_worker")
    mailbox = AgentMailboxStore()
    status = mailbox.publish(
        "status",
        worker,
        status="completed",
        submission_id="submission_one",
    )
    message = mailbox.publish(
        "message",
        root,
        recipient=worker,
        message="continue with the API only",
    )
    assert mailbox.take_updates(root.agent_id, {worker.agent_id}) == (status,)

    restored = AgentMailboxStore.from_snapshot(mailbox.snapshot())

    assert restored.take_updates(root.agent_id, {worker.agent_id}) == ()
    assert restored.take_messages(worker.agent_id) == (message,)
    following = restored.publish(
        "status",
        worker,
        status="completed",
        submission_id="submission_two",
    )
    assert following.sequence == message.sequence + 1
