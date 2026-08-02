# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from mind_app.modes.result import RunResult
from mind_app.history.transcript import ConversationTranscriptStore
from mind_app.output.silent import create_silent_output_session
from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.hooks.scope import (
    HookExecutionScope,
)
from mind_app.runtime.subagents.control import AgentStateError
from mind_app.runtime.subagents.runtime import SubagentRuntime
from mind_core.agent_config import AgentSettings
from mind_core.permissions import preset_permissions
from mind_nova.identifiers import new_cid, new_sid


class _Controller:
    def __init__(self) -> None:
        self.configs = []
        self.stream_calls = []
        self.stream_handler = None
        self.config_session = SimpleNamespace(load=lambda: {
            "skills": {"enabled": ["__test_none__"], "disabled": []},
        })

    def hook_scope(self, context):
        return HookExecutionScope.empty(context)

    async def with_mcp_session(self, pref_config, function):
        self.configs.append(pref_config)
        return await function(
            "session",
            [{"name": "tool", "meta": {"domain": "coding"}}],
        )

    async def stream_looper(self, **kwargs):
        self.stream_calls.append(kwargs)
        if self.stream_handler is not None:
            return await self.stream_handler(**kwargs)
        return RunResult(status="completed")

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


def _parent_turn(*, transcript_path: str = "") -> TurnContext:
    cid = new_cid()
    sid = new_sid(cid)
    return TurnContext.create(
        agent=AgentContext.root(sid),
        cid=cid,
        sid=sid,
        mode="xtra",
        source="test",
        pref_config={"primary": {"model": "parent-model"}},
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
        transcript_path=transcript_path,
        turn_id="parent_turn",
    )


@pytest.mark.anyio
async def test_runtime_keeps_thread_context_across_submissions() -> None:
    controller = _Controller()
    parent = _parent_turn()
    pref_config = {
        "primary": {"model": "child-model"},
        "routing": {"tags": ["review"]},
    }
    skill_payload = [{"name": "review", "description": "Review changes"}]
    runtime = SubagentRuntime(
        controller,
        skills_provider=lambda: skill_payload,
    )
    spawned = await runtime.spawn(
        parent,
        "first task",
        pref_config,
        agent_type="review",
        task_name="review",
        agent_id="agent_review",
    )
    pref_config["primary"]["model"] = "mutated"
    pref_config["routing"]["tags"].append("unexpected")
    skill_payload[0]["description"] = "mutated"
    with pytest.raises(TypeError):
        spawned.thread.pref_config["primary"]["model"] = "forbidden"
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    await runtime.submit(
        parent.sid,
        spawned.agent_id,
        "second task",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    assert len(controller.stream_calls) == 2
    first_call, second_call = controller.stream_calls
    first = first_call["turn_execution"]
    second = second_call["turn_execution"]
    assert first_call["session"] == "session"
    assert first_call["tools"] == [
        {"name": "tool", "meta": {"domain": "coding"}},
    ]
    assert first_call["session_factory"] is create_silent_output_session
    assert first_call["skills"] == [{
        "name": "review",
        "description": "Review changes",
    }]
    assert second_call["skills"] == first_call["skills"]
    assert first.context.agent is second.context.agent
    assert first.context.cid == second.context.cid
    assert first.context.sid == second.context.sid
    assert first.context.turn_id != second.context.turn_id
    assert first.context.cwd == parent.cwd
    assert first.context.permissions == parent.permissions
    assert first.context.session_started
    assert not second.context.session_started
    assert first.metadata["parent_turn_id"] == parent.turn_id
    assert first.metadata["turn_index"] == 1
    assert second.metadata["turn_index"] == 2
    assert controller.configs == [
        {
            "primary": {"model": "child-model"},
            "routing": {"tags": ["review"]},
        },
        {
            "primary": {"model": "child-model"},
            "routing": {"tags": ["review"]},
        },
    ]
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_assigns_stable_child_transcript_path(tmp_path) -> None:
    controller = _Controller()
    parent = _parent_turn(
        transcript_path=str(tmp_path / "root.log"),
    )
    store = ConversationTranscriptStore(tmp_path / "sessions")
    runtime = SubagentRuntime(
        controller,
        transcript_path_for=store.path_for_session,
    )

    spawned = await runtime.spawn(
        parent,
        "inspect",
        {},
        agent_type="review",
        task_name="inspect",
        agent_id="agent_review",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    first = controller.stream_calls[0]["turn_execution"]

    await runtime.submit(parent.sid, spawned.agent_id, "inspect again")
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    second = controller.stream_calls[1]["turn_execution"]

    assert first.context.transcript_path == second.context.transcript_path
    assert first.context.transcript_path != parent.transcript_path
    child_path = Path(first.context.transcript_path)
    assert child_path.is_relative_to(tmp_path / "sessions")
    assert child_path.name == f"session-{first.context.sid}.jsonl"
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_forks_recent_parent_turns_into_first_child_turn(tmp_path) -> None:
    controller = _Controller()
    parent_path = tmp_path / "parent.jsonl"
    writer = ConversationTranscriptStore.writer(
        parent_path,
        session_id="sid_parent",
        turn_id="turn_parent",
    )
    writer.open()
    writer.append(
        "message.created",
        actor="user",
        payload={"content": "parent task"},
    )
    writer.append(
        "message.created",
        actor="assistant",
        payload={"content": "parent result"},
    )
    writer.close()

    runtime = SubagentRuntime(
        controller,
        transcript_path_for=ConversationTranscriptStore(
            tmp_path / "sessions"
        ).path_for_session,
    )
    parent = _parent_turn(transcript_path=str(parent_path))

    spawned = await runtime.spawn(
        parent,
        "child task",
        {},
        agent_type="review",
        task_name="review",
        fork_turns="1",
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)
    execution = controller.stream_calls[-1]["turn_execution"]

    assert "parent task" in execution.additional_context[0]
    assert execution.metadata["task_path"] == "/root/review"
    assert execution.metadata["fork_turns"] == "1"
    await runtime.shutdown()


@pytest.mark.anyio
async def test_silent_output_session_records_transcript(tmp_path) -> None:
    transcript = tmp_path / "agent.log"
    session = create_silent_output_session(str(transcript))

    await session.control.open()
    await session.control.record_hidden_output("child result")
    await session.control.stop()

    assert transcript.read_text(encoding="utf-8") == "child result\n"


@pytest.mark.anyio
async def test_runtime_isolates_controls_by_root_session() -> None:
    runtime = SubagentRuntime(_Controller())
    parents = (_parent_turn(), _parent_turn())

    snapshots = []
    for parent in parents:
        snapshots.append(await runtime.spawn(
            parent,
            "task",
            {},
            agent_type="worker",
            task_name="worker",
            agent_id="same_agent_id",
        ))

    for parent, snapshot in zip(parents, snapshots):
        result = await runtime.wait(
            parent.sid,
            [snapshot.agent_id],
            timeout_sec=1,
        )
        assert result.snapshots[0].status == "completed"
        assert len(await runtime.snapshots(parent.sid)) == 1

    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_shutdown_cancels_tasks_and_is_terminal() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def operation(**_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    controller.stream_handler = operation

    await runtime.spawn(
        parent,
        "blocking task",
        {},
        agent_type="worker",
        task_name="blocking",
    )
    await started.wait()

    await runtime.shutdown()

    assert stopped.is_set()
    with pytest.raises(AgentStateError, match="shut down"):
        await runtime.spawn(
            parent,
            "late task",
            {},
            agent_type="worker",
            task_name="late",
        )


@pytest.mark.anyio
async def test_disabled_runtime_rejects_spawn() -> None:
    runtime = SubagentRuntime(
        _Controller(),
        settings=AgentSettings(enabled=False),
    )

    with pytest.raises(AgentStateError, match="disabled"):
        await runtime.spawn(
            _parent_turn(),
            "task",
            {},
            agent_type="worker",
            task_name="disabled",
        )


@pytest.mark.anyio
async def test_runtime_marks_failed_model_result_as_failed() -> None:
    controller = _Controller()

    async def fail(**_kwargs):
        return RunResult(status="failed", error="model request failed")

    controller.stream_handler = fail
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()

    spawned = await runtime.spawn(
        parent,
        "failing task",
        {},
        agent_type="worker",
        task_name="failing",
    )
    waited = await runtime.wait(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=1,
    )

    assert waited.snapshots[0].status == "failed"
    assert "model request failed" in waited.snapshots[0].error
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_reuses_thread_after_executor_exception() -> None:
    controller = _Controller()

    async def fail(**_kwargs):
        raise RuntimeError("stream crashed")

    controller.stream_handler = fail
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    spawned = await runtime.spawn(
        parent,
        "failing task",
        {},
        agent_type="worker",
        task_name="failing",
    )
    failed = await runtime.wait(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=1,
    )

    assert failed.snapshots[0].status == "failed"
    assert failed.snapshots[0].error == "RuntimeError: stream crashed"

    controller.stream_handler = None
    await runtime.submit(parent.sid, spawned.agent_id, "retry task")
    retried = await runtime.wait(
        parent.sid,
        [spawned.agent_id],
        timeout_sec=1,
    )

    assert retried.snapshots[0].status == "completed"
    assert retried.snapshots[0].result.status == "completed"
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_coordinates_two_agents_through_interrupt_resume_and_exit() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    started = {
        "agent_first": asyncio.Event(),
        "agent_second": asyncio.Event(),
    }
    release_second = asyncio.Event()
    exit_turn_started = asyncio.Event()
    exit_turn_cancelled = asyncio.Event()

    async def execute(**kwargs):
        execution = kwargs["turn_execution"]
        agent_id = execution.context.agent.agent_id
        message = execution.message

        if message == "retry task":
            return RunResult(status="completed", assistant_text="retried")
        if message == "wait for exit":
            exit_turn_started.set()
            try:
                await asyncio.Future()
            finally:
                exit_turn_cancelled.set()

        started[agent_id].set()
        if agent_id == "agent_first":
            await asyncio.Future()
        await release_second.wait()
        return RunResult(status="completed", assistant_text="second done")

    controller.stream_handler = execute
    first = await runtime.spawn(
        parent,
        "first task",
        {},
        agent_type="review",
        task_name="first",
        agent_id="agent_first",
    )
    second = await runtime.spawn(
        parent,
        "second task",
        {},
        agent_type="test",
        task_name="second",
        agent_id="agent_second",
    )
    await asyncio.gather(*(event.wait() for event in started.values()))

    interrupted = await runtime.interrupt(parent.sid, first.agent_id)
    assert interrupted.status == "interrupted"

    release_second.set()
    second_result = await runtime.wait(
        parent.sid,
        [second.agent_id],
        timeout_sec=1,
    )
    assert second_result.snapshots[0].status == "completed"
    assert second_result.snapshots[0].result.assistant_text == "second done"

    await runtime.close(parent.sid, first.agent_id)
    resumed = await runtime.resume(parent.sid, first.agent_id)
    assert resumed.status == "interrupted"
    await runtime.submit(parent.sid, first.agent_id, "retry task")
    retried = await runtime.wait(
        parent.sid,
        [first.agent_id],
        timeout_sec=1,
    )
    assert retried.snapshots[0].result.assistant_text == "retried"

    await runtime.submit(parent.sid, second.agent_id, "wait for exit")
    await exit_turn_started.wait()
    await runtime.shutdown()

    assert exit_turn_cancelled.is_set()
    with pytest.raises(AgentStateError, match="shut down"):
        await runtime.spawn(
            parent,
            "late task",
            {},
            agent_type="worker",
            task_name="late",
        )
