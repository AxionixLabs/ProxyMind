# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_core.permissions import PermissionSettings
from mind_nova.identifiers import new_cid, new_sid
from mind_app.runtime.execution import AgentContext
from mind_app.runtime.subagents.control import (
    AgentControl,
    AgentDepthError,
    AgentLimitError,
    AgentSubmission,
    AgentStateError,
)
from mind_app.runtime.subagents.thread import AgentThreadContext


class _TestControl(AgentControl):
    def __init__(self, *args, **kwargs) -> None:
        self._operations = {}
        super().__init__(*args, executor=self._execute, **kwargs)

    def submission(self, operation, *, kind):
        submission = AgentSubmission.create(
            f"operation-{len(self._operations) + 1}",
            kind=kind,
        )
        self._operations[submission.submission_id] = operation
        return submission

    async def _execute(self, context, submission):
        return await self._operations[submission.submission_id](context)


def _control(
    *,
    max_open_agents: int = 4,
    max_depth: int = 2,
) -> _TestControl:
    return _TestControl(
        AgentContext.root("sid_root"),
        max_open_agents=max_open_agents,
        max_depth=max_depth,
    )


def _thread(
    parent: AgentContext,
    agent_type: str,
    *,
    agent_id: str | None = None,
) -> AgentThreadContext:
    cid = new_cid()
    return AgentThreadContext(
        agent=parent.child(
            agent_type,
            str(agent_id or agent_type).removeprefix("agent_") or agent_type,
            agent_id=agent_id,
        ),
        cid=cid,
        sid=new_sid(cid),
        source="subagent",
        cwd="D:/workspace",
        permissions=PermissionSettings("workspace-write", "on-request"),
        pref_config={},
        spawn_turn_id="parent_turn",
    )


async def _spawn(
    control: _TestControl,
    parent: AgentContext,
    agent_type: str,
    operation,
    *,
    agent_id: str | None = None,
):
    return await control.spawn(
        _thread(parent, agent_type, agent_id=agent_id),
        control.submission(operation, kind="initial"),
    )


@pytest.mark.anyio
async def test_agent_control_runs_child_and_records_result() -> None:
    control = _control()
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation(context):
        assert context.agent.parent_agent_id == "root"
        assert context.agent.root_session_id == "sid_root"
        assert context.turn_index == 1
        started.set()
        await release.wait()
        return {"answer": 42}

    spawned = await _spawn(
        control,
        control.root,
        "explorer",
        operation,
        agent_id="agent_one",
    )

    assert spawned.status == "pending"
    await started.wait()
    assert (await control.get(spawned.agent_id)).status == "running"

    release.set()
    waited = await control.wait([spawned.agent_id], timeout_sec=1)

    assert not waited.timed_out
    assert len(waited.snapshots) == 1
    assert waited.snapshots[0].status == "completed"
    assert waited.snapshots[0].submission is not None
    assert waited.snapshots[0].submission.kind == "initial"
    assert waited.snapshots[0].turn_count == 1
    assert waited.snapshots[0].queued_count == 0
    assert waited.snapshots[0].result == {"answer": 42}


@pytest.mark.anyio
async def test_agent_control_reuses_open_agent_for_new_submission() -> None:
    control = _control()

    first = await _spawn(
        control,
        control.root,
        "worker",
        lambda context: _return_value("first"),
        agent_id="agent_reused",
    )
    first_result = await control.wait([first.agent_id], timeout_sec=1)
    first_submission = first_result.snapshots[0].submission_id

    submission_id = await control.submit(
        first.agent_id,
        control.submission(
            lambda context: _return_value("second"),
            kind="followup",
        ),
    )
    second_result = await control.wait([first.agent_id], timeout_sec=1)

    assert submission_id != first_submission
    assert second_result.snapshots[0].status == "completed"
    assert second_result.snapshots[0].result == "second"


@pytest.mark.anyio
async def test_completed_agent_keeps_slot_until_closed() -> None:
    control = _control(max_open_agents=1)

    first = await _spawn(
        control,
        control.root,
        "worker",
        lambda context: _return_value("done"),
        agent_id="agent_first",
    )
    await control.wait([first.agent_id], timeout_sec=1)

    with pytest.raises(AgentLimitError, match="limit reached"):
        await _spawn(
            control,
            control.root,
            "worker",
            lambda context: _return_value("blocked"),
        )

    assert await control.count_open() == 1
    assert (await control.close(first.agent_id)).status == "completed"
    assert (await control.get(first.agent_id)).status == "closed"
    assert await control.count_open() == 0

    second = await _spawn(
        control,
        control.root,
        "worker",
        lambda context: _return_value("new"),
        agent_id="agent_second",
    )
    assert second.status == "pending"
    await control.close(second.agent_id)


@pytest.mark.anyio
async def test_agent_control_records_operation_failure() -> None:
    control = _control()

    async def fail(context):
        raise RuntimeError("operation failed")

    spawned = await _spawn(
        control,
        control.root,
        "reviewer",
        fail,
        agent_id="agent_failed",
    )
    waited = await control.wait([spawned.agent_id], timeout_sec=1)
    snapshot = waited.snapshots[0]

    assert snapshot.status == "failed"
    assert snapshot.result is None
    assert snapshot.error == "RuntimeError: operation failed"


@pytest.mark.anyio
async def test_agent_wait_returns_first_final_target_and_times_out() -> None:
    control = _control()
    first_release = asyncio.Event()
    second_release = asyncio.Event()

    first = await _spawn(
        control,
        control.root,
        "worker",
        lambda context: first_release.wait(),
        agent_id="agent_slow",
    )
    second = await _spawn(
        control,
        control.root,
        "worker",
        lambda context: second_release.wait(),
        agent_id="agent_fast",
    )

    timed_out = await control.wait([first.agent_id], timeout_sec=0.01)
    assert timed_out.timed_out
    assert timed_out.snapshots == ()

    second_release.set()
    waited = await control.wait(
        [first.agent_id, second.agent_id],
        timeout_sec=1,
    )

    assert not waited.timed_out
    assert [snapshot.agent_id for snapshot in waited.snapshots] == [
        second.agent_id,
    ]
    await control.close_all()


@pytest.mark.anyio
async def test_interrupt_cancels_active_turn_without_closing_agent() -> None:
    control = _control()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def operation(context):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    spawned = await _spawn(
        control,
        control.root,
        "worker",
        operation,
        agent_id="agent_interrupt",
    )
    await started.wait()

    interrupted = await control.interrupt(spawned.agent_id)

    assert interrupted.status == "interrupted"
    assert stopped.is_set()
    assert await control.count_open() == 1
    await control.close(spawned.agent_id)


@pytest.mark.anyio
async def test_submit_queues_until_active_turn_cleanup_finishes() -> None:
    control = _control()
    started = asyncio.Event()
    release = asyncio.Event()
    second_started = asyncio.Event()
    timeline = []

    async def operation(context):
        timeline.append("first-start")
        started.set()
        await release.wait()
        timeline.append("first-stop")
        return "first"

    async def follow_up(context):
        timeline.append("second-start")
        second_started.set()
        return "second"

    spawned = await _spawn(
        control,
        control.root,
        "worker",
        operation,
        agent_id="agent_queued",
    )
    await started.wait()

    submission_id = await control.submit(
        spawned.agent_id,
        control.submission(follow_up, kind="followup"),
    )
    await asyncio.sleep(0)
    assert not second_started.is_set()
    queued = await control.get(spawned.agent_id)
    assert queued.turn_count == 1
    assert queued.queued_count == 1

    release.set()
    result = await control.wait([spawned.agent_id], timeout_sec=1)

    assert submission_id == result.snapshots[0].submission_id
    assert result.snapshots[0].turn_count == 2
    assert result.snapshots[0].queued_count == 0
    assert result.snapshots[0].result == "second"
    assert timeline == ["first-start", "first-stop", "second-start"]


@pytest.mark.anyio
async def test_interrupting_submission_runs_after_cancelled_turn_cleanup() -> None:
    control = _control()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    follow_up_started = asyncio.Event()
    timeline = []

    async def operation(context):
        timeline.append("first-start")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            timeline.append("first-stop")
            cancelled.set()

    async def follow_up(context):
        assert cancelled.is_set()
        timeline.append("second-start")
        follow_up_started.set()
        return "redirected"

    spawned = await _spawn(
        control,
        control.root,
        "worker",
        operation,
        agent_id="agent_redirected",
    )
    await started.wait()

    submission_id = await control.submit(
        spawned.agent_id,
        control.submission(follow_up, kind="followup"),
        interrupt=True,
    )
    await follow_up_started.wait()
    result = await control.wait([spawned.agent_id], timeout_sec=1)

    assert submission_id == result.snapshots[0].submission_id
    assert result.snapshots[0].result == "redirected"
    assert timeline == ["first-start", "first-stop", "second-start"]


@pytest.mark.anyio
async def test_interrupt_during_terminal_commit_still_advances_queue() -> None:
    control = _control()
    commit_started = asyncio.Event()
    original_finish = control._finish
    finish_calls = [0]

    async def delayed_finish(*args, **kwargs):
        finish_calls[0] += 1
        if finish_calls[0] == 1:
            commit_started.set()
            await asyncio.Event().wait()
        return await original_finish(*args, **kwargs)

    control._finish = delayed_finish
    spawned = await _spawn(
        control,
        control.root,
        "worker",
        lambda context: _return_value("first"),
        agent_id="agent_commit_race",
    )
    await commit_started.wait()

    submission_id = await control.submit(
        spawned.agent_id,
        control.submission(
            lambda context: _return_value("redirected"),
            kind="followup",
        ),
        interrupt=True,
    )
    result = await control.wait([spawned.agent_id], timeout_sec=1)

    assert submission_id == result.snapshots[0].submission_id
    assert result.snapshots[0].status == "completed"
    assert result.snapshots[0].result == "redirected"


@pytest.mark.anyio
async def test_closed_agent_can_resume_with_same_thread_context() -> None:
    control = _control(max_open_agents=1)
    spawned = await _spawn(
        control,
        control.root,
        "worker",
        lambda context: _return_value("first"),
        agent_id="agent_resumed",
    )
    await control.wait([spawned.agent_id], timeout_sec=1)

    previous = await control.close(spawned.agent_id)
    resumed = await control.resume(spawned.agent_id)
    submission_id = await control.submit(
        spawned.agent_id,
        control.submission(
            lambda context: _return_value("second"),
            kind="followup",
        ),
    )
    result = await control.wait([spawned.agent_id], timeout_sec=1)

    assert previous.status == "completed"
    assert resumed.status == "completed"
    assert resumed.context is spawned.context
    assert submission_id == result.snapshots[0].submission_id
    assert result.snapshots[0].result == "second"


@pytest.mark.anyio
async def test_agent_cannot_resume_until_close_cleanup_finishes() -> None:
    control = _control()
    started = asyncio.Event()
    cancellation_seen = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def operation(context):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await finish_cleanup.wait()

    spawned = await _spawn(
        control,
        control.root,
        "worker",
        operation,
        agent_id="agent_resume_race",
    )
    await started.wait()

    closing = asyncio.create_task(control.close(spawned.agent_id))
    await cancellation_seen.wait()

    with pytest.raises(AgentStateError, match="still closing"):
        await control.resume(spawned.agent_id)

    finish_cleanup.set()
    await closing

    resumed = await control.resume(spawned.agent_id)
    assert resumed.status == "interrupted"


@pytest.mark.anyio
async def test_closing_task_keeps_slot_until_cleanup_finishes() -> None:
    control = _control(max_open_agents=1)
    started = asyncio.Event()
    cancellation_seen = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def operation(context):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await finish_cleanup.wait()

    spawned = await _spawn(
        control,
        control.root,
        "worker",
        operation,
        agent_id="agent_closing",
    )
    await started.wait()

    close = asyncio.create_task(control.close(spawned.agent_id))
    await cancellation_seen.wait()

    with pytest.raises(AgentLimitError, match="limit reached"):
        await _spawn(
            control,
            control.root,
            "worker",
            lambda context: _return_value(None),
        )

    finish_cleanup.set()
    assert (await close).status == "running"
    assert (await control.get(spawned.agent_id)).status == "closed"
    assert await control.count_open() == 0


@pytest.mark.anyio
async def test_closing_parent_cancels_complete_subtree() -> None:
    control = _control(max_open_agents=3, max_depth=2)
    parent_started = asyncio.Event()
    child_started = asyncio.Event()

    async def parent_operation(context):
        parent_started.set()
        await asyncio.Event().wait()

    async def child_operation(context):
        child_started.set()
        await asyncio.Event().wait()

    parent = await _spawn(
        control,
        control.root,
        "worker",
        parent_operation,
        agent_id="agent_parent",
    )
    await parent_started.wait()
    child = await _spawn(
        control,
        parent.context,
        "explorer",
        child_operation,
        agent_id="agent_child",
    )
    await child_started.wait()

    closed = await control.close(parent.agent_id)
    snapshots = await control.snapshots()

    assert closed.status == "running"
    assert {snapshot.agent_id: snapshot.status for snapshot in snapshots} == {
        parent.agent_id: "closed",
        child.agent_id: "closed",
    }
    assert await control.count_open() == 0


@pytest.mark.anyio
async def test_agent_control_enforces_depth_and_root_session() -> None:
    control = _control(max_depth=1)
    parent = await _spawn(
        control,
        control.root,
        "worker",
        lambda context: _return_value(None),
        agent_id="agent_parent",
    )
    await control.wait([parent.agent_id], timeout_sec=1)

    with pytest.raises(AgentDepthError, match="exceeds limit"):
        await _spawn(
            control,
            parent.context,
            "worker",
            lambda context: _return_value(None),
        )

    with pytest.raises(AgentStateError, match="another root session"):
        await _spawn(
            control,
            AgentContext.root("sid_other"),
            "worker",
            lambda context: _return_value(None),
        )


async def _return_value(value):
    return value
