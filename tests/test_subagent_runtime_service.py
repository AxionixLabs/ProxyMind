# -*- coding: utf-8 -*-

import asyncio

import pytest

from mind_app.modes.result import RunResult
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

    def hook_scope(self, context):
        return HookExecutionScope.empty(context)

    async def with_mcp_session(self, pref_config, function):
        self.configs.append(pref_config)
        return await function("session", [{"name": "tool"}])

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
    runtime = SubagentRuntime(controller)
    parent = _parent_turn()
    pref_config = {
        "primary": {"model": "child-model"},
        "skills": {"enabled": ["review"]},
    }
    executions = []

    async def operation(execution, session, tools, report):
        executions.append(execution)
        assert session == "session"
        assert tools == [{"name": "tool"}]
        return RunResult(status="completed")

    spawned = await runtime.spawn(
        parent,
        "first task",
        pref_config,
        operation,
        agent_type="review",
        agent_id="agent_review",
    )
    pref_config["primary"]["model"] = "mutated"
    pref_config["skills"]["enabled"].append("unexpected")
    with pytest.raises(TypeError):
        spawned.thread.pref_config["primary"]["model"] = "forbidden"
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    await runtime.submit(
        parent.sid,
        spawned.agent_id,
        "second task",
        operation,
    )
    await runtime.wait(parent.sid, [spawned.agent_id], timeout_sec=1)

    assert len(executions) == 2
    first, second = executions
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
            "skills": {"enabled": ["review"]},
        },
        {
            "primary": {"model": "child-model"},
            "skills": {"enabled": ["review"]},
        },
    ]
    await runtime.shutdown()


@pytest.mark.anyio
async def test_runtime_isolates_controls_by_root_session() -> None:
    runtime = SubagentRuntime(_Controller())
    parents = (_parent_turn(), _parent_turn())

    async def operation(*_args):
        return RunResult(status="completed")

    snapshots = []
    for parent in parents:
        snapshots.append(await runtime.spawn(
            parent,
            "task",
            {},
            operation,
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
    runtime = SubagentRuntime(_Controller())
    parent = _parent_turn()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def operation(*_args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    await runtime.spawn(
        parent,
        "blocking task",
        {},
        operation,
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
            operation,
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
            lambda *_args: None,
            agent_type="worker",
        )
