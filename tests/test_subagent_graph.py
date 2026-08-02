# -*- coding: utf-8 -*-

import asyncio
from dataclasses import replace

import pytest

from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.subagents.control import (
    AgentControl,
    AgentGraphCheckpoint,
    AgentGraphRecord,
    AgentSubmission,
)
from mind_app.runtime.subagents.graph import (
    AgentGraphPersistence,
    AgentGraphStore,
)
from mind_app.runtime.subagents.mailbox import AgentMailboxStore
from mind_app.runtime.subagents.thread import AgentThreadContext
from mind_core.permissions import preset_permissions
from mind_nova.identifiers import new_cid, new_sid


def _thread() -> AgentThreadContext:
    cid = new_cid()
    root_sid = new_sid(cid)
    parent = TurnContext.create(
        agent=AgentContext.root(root_sid),
        cid=cid,
        sid=root_sid,
        source="test",
        pref_config={"primary": {"model": "parent"}},
        cwd="/workspace",
        permissions=preset_permissions("auto"),
        turn_id="turn_parent",
    )
    return AgentThreadContext.child(
        parent,
        "worker",
        "worker",
        {
            "primary": {"model": "child"},
            "routing": {"tags": ["code", "test"]},
        },
        skills=({"name": "review", "description": "Review changes"},),
        agent_id="agent_worker",
    )


def _checkpoint(revision: int) -> AgentGraphCheckpoint:
    thread = _thread()
    active = AgentSubmission.create(
        "implement feature",
        kind="initial",
        parent_turn_id="turn_parent",
    )
    queued = AgentSubmission.create(
        "run tests",
        kind="followup",
        parent_turn_id="turn_followup",
    )
    return AgentGraphCheckpoint(
        root_session_id=thread.agent.root_session_id,
        revision=revision,
        updated_at_ms=1_700_000_000_000 + revision,
        records=(AgentGraphRecord(
            thread=thread,
            status="running",
            submission=active,
            queue=(queued,),
            turn_count=1,
        ),),
    )


def test_graph_store_round_trips_latest_checkpoint(tmp_path) -> None:
    store = AgentGraphStore(tmp_path / "agents.db")
    latest = _checkpoint(2)
    mailbox = AgentMailboxStore()
    message = mailbox.publish(
        "message",
        AgentContext.root(latest.root_session_id),
        recipient=latest.records[0].thread.agent,
        message="preserve this constraint",
    )
    latest = replace(latest, mailbox=mailbox.snapshot())
    stale = AgentGraphCheckpoint(
        root_session_id=latest.root_session_id,
        revision=1,
        updated_at_ms=latest.updated_at_ms - 1,
        records=latest.records,
    )

    assert store.save(latest)
    assert not store.save(stale)

    restored = store.load(latest.root_session_id)
    assert restored is not None
    assert restored.revision == 2
    assert restored.records == latest.records
    assert restored.records[0].thread.config_snapshot() == {
        "primary": {"model": "child"},
        "routing": {"tags": ["code", "test"]},
    }
    assert restored.records[0].queue[0].message == "run tests"
    restored_mailbox = AgentMailboxStore.from_snapshot(restored.mailbox)
    assert restored_mailbox.take_messages("agent_worker") == (message,)


@pytest.mark.anyio
async def test_control_restores_active_turn_without_replaying_it() -> None:
    checkpoint = _checkpoint(4)
    calls = []
    published = []

    async def execute(_turn, submission):
        calls.append(submission.message)
        return submission.message

    control = AgentControl.restore(
        checkpoint,
        execute,
        checkpoint_publisher=published.append,
    )
    restored = await control.get("/root/worker")

    assert restored.status == "interrupted_by_restart"
    assert restored.error == "agent execution interrupted by process restart"
    assert restored.queued_count == 1
    assert calls == []
    assert published[0].revision == checkpoint.revision + 1
    assert published[0].records[0].status == "interrupted_by_restart"

    updates = await control.wait_updates(
        ["/root/worker"],
        caller=AgentContext.root(checkpoint.root_session_id),
        timeout_sec=0,
    )
    assert [event.status for event in updates.events] == [
        "interrupted_by_restart",
    ]

    await control.followup(
        "worker",
        AgentSubmission.create("new task", kind="followup"),
    )
    result = await control.wait(["worker"], timeout_sec=1)

    assert result.snapshots[0].status == "completed"
    assert calls == ["run tests", "new task"]


@pytest.mark.anyio
async def test_graph_persistence_coalesces_pending_revisions() -> None:
    saved = []

    class _Store:
        def save(self, checkpoint):
            saved.append(checkpoint)
            return True

    persistence = AgentGraphPersistence(_Store())
    first = _checkpoint(1)
    latest = AgentGraphCheckpoint(
        root_session_id=first.root_session_id,
        revision=2,
        updated_at_ms=first.updated_at_ms + 1,
        records=first.records,
    )

    persistence.publish(first)
    persistence.publish(latest)
    await persistence.close()

    assert [(item.root_session_id, item.revision) for item in saved] == [
        (latest.root_session_id, 2),
    ]


@pytest.mark.anyio
async def test_agent_control_publishes_queue_and_terminal_checkpoints() -> None:
    thread = _thread()
    started = asyncio.Event()
    release = asyncio.Event()
    checkpoints = []

    async def execute(_turn, submission):
        if submission.kind == "initial":
            started.set()
            await release.wait()
        return submission.message

    control = AgentControl(
        AgentContext.root(thread.agent.root_session_id),
        execute,
        checkpoint_publisher=checkpoints.append,
    )
    spawned = await control.spawn(
        thread,
        AgentSubmission.create("first", kind="initial"),
    )
    await started.wait()
    await control.followup(
        spawned.agent_id,
        AgentSubmission.create("second", kind="followup"),
    )

    queued = checkpoints[-1].records[0]
    assert queued.status == "running"
    assert [item.message for item in queued.queue] == ["second"]

    release.set()
    result = await control.wait([spawned.agent_id], timeout_sec=1)

    assert result.snapshots[0].status == "completed"
    final = checkpoints[-1].records[0]
    assert final.status == "completed"
    assert final.submission is not None
    assert final.submission.message == "second"
    assert final.turn_count == 2
    assert final.queue == ()
    assert [item.revision for item in checkpoints] == list(
        range(1, len(checkpoints) + 1)
    )
