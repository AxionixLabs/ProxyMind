# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from mind_app.modes.result import RunResult
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
        return await function("session", [{"name": "tool"}])

    async def stream_looper(self, **kwargs):
        self.stream_calls.append(kwargs)
        if self.stream_handler is not None:
            return await self.stream_handler(**kwargs)
        return RunResult(status="completed")

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


def _parent_turn() -> TurnContext:
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
    assert first_call["tools"] == [{"name": "tool"}]
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
