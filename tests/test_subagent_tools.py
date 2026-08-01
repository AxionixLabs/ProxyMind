# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from mind_app.client_tools.registry import (
    ClientToolRegistry,
    default_registry,
)
from mind_app.client_tools.subagents import subagent_tools
from mind_app.mcp.session_adapter import CompositeToolSession
from mind_app.modes.result import RunResult
from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.hooks.scope import HookExecutionScope
from mind_app.runtime.subagents.runtime import SubagentRuntime
from mind_core.agent_config import AgentSettings
from mind_core.permissions import preset_permissions
from mind_nova.identifiers import new_cid, new_sid


class _Controller:
    def __init__(self) -> None:
        self.messages = []
        self.stream_handler = None
        self.config_session = SimpleNamespace(load=lambda: {})

    def hook_scope(self, context):
        return HookExecutionScope.empty(context)

    async def with_mcp_session(self, pref_config, function):
        return await function("session", [])

    async def stream_looper(self, **kwargs):
        execution = kwargs["turn_execution"]
        self.messages.append(execution.message)
        if self.stream_handler is not None:
            return await self.stream_handler(**kwargs)
        return RunResult(
            status="completed",
            assistant_text=f"done: {execution.message}",
        )

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


def _root_turn() -> TurnContext:
    cid = new_cid()
    sid = new_sid(cid)
    return TurnContext.create(
        agent=AgentContext.root(sid),
        cid=cid,
        sid=sid,
        mode="xtra",
        source="test",
        pref_config={},
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
        turn_id="root_turn",
    )


def _session(runtime: SubagentRuntime) -> CompositeToolSession:
    return CompositeToolSession(
        client_registry=ClientToolRegistry(subagent_tools(runtime)),
    )


async def _call(
    session: CompositeToolSession,
    turn: TurnContext,
    name: str,
    arguments: dict,
    pref_config: dict,
):
    return await session.call_tool(
        name,
        arguments,
        turn_context=turn,
        pref_config=pref_config,
    )


def _data(result) -> dict:
    return result.structuredContent["data"]


def test_default_registry_exposes_agent_tools_only_when_enabled(tmp_path) -> None:
    enabled = SubagentRuntime(_Controller())
    disabled = SubagentRuntime(
        _Controller(),
        settings=AgentSettings(enabled=False),
    )

    enabled_names = {
        tool.name
        for tool in default_registry(
            execution_root=tmp_path,
            subagent_runtime=enabled,
        ).list_tools().tools
    }
    disabled_names = {
        tool.name
        for tool in default_registry(
            execution_root=tmp_path,
            subagent_runtime=disabled,
        ).list_tools().tools
    }

    assert {
        "spawn_agent",
        "send_input",
        "resume_agent",
        "wait_agent",
        "close_agent",
    } <= enabled_names
    assert "spawn_agent" not in disabled_names


@pytest.mark.anyio
async def test_agent_tools_preserve_config_across_close_resume_and_send() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    session = _session(runtime)
    turn = _root_turn()
    pref_config = {
        "primary": {"model": "child-model"},
        "routing": {"tags": ["review"]},
    }

    spawned = await _call(
        session,
        turn,
        "spawn_agent",
        {
            "message": "first",
            "task_name": "review",
            "agent_type": "review",
        },
        pref_config,
    )
    agent_id = _data(spawned)["agent_id"]
    pref_config["primary"]["model"] = "mutated"
    pref_config["routing"]["tags"].append("unexpected")

    waited = await _call(
        session,
        turn,
        "wait_agent",
        {"targets": [agent_id]},
        pref_config,
    )
    snapshot = await runtime.get(turn.sid, agent_id)

    assert not spawned.isError
    assert _data(waited) == {
        "status": {agent_id: {"completed": "done: first"}},
        "timed_out": False,
    }
    assert snapshot.thread.config_snapshot() == {
        "primary": {"model": "child-model"},
        "routing": {"tags": ["review"]},
    }

    closed = await _call(
        session,
        turn,
        "close_agent",
        {"target": agent_id},
        pref_config,
    )
    rejected = await _call(
        session,
        turn,
        "send_input",
        {"target": agent_id, "message": "blocked"},
        pref_config,
    )
    resumed = await _call(
        session,
        turn,
        "resume_agent",
        {"id": agent_id},
        pref_config,
    )
    submitted = await _call(
        session,
        turn,
        "send_input",
        {"target": agent_id, "message": "second"},
        pref_config,
    )
    second = await _call(
        session,
        turn,
        "wait_agent",
        {"targets": [agent_id]},
        pref_config,
    )

    assert _data(closed) == {
        "previous_status": {"completed": "done: first"},
    }
    assert rejected.isError
    assert "agent is closed" in _data(rejected)["error"]
    assert _data(resumed) == {
        "status": {"completed": None},
    }
    assert _data(submitted)["submission_id"]
    assert _data(second)["status"] == {
        agent_id: {"completed": "done: second"},
    }
    assert controller.messages == ["first", "second"]

    await runtime.shutdown()


@pytest.mark.anyio
async def test_send_input_queues_and_interrupts_without_overlapping_turns() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    session = _session(runtime)
    turn = _root_turn()
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    second_started = asyncio.Event()
    second_release = asyncio.Event()
    cancelled = asyncio.Event()
    timeline = []

    async def stream_handler(**kwargs):
        message = kwargs["turn_execution"].message
        timeline.append(f"{message}-start")
        if message == "first":
            first_started.set()
            await first_release.wait()
        elif message == "second":
            second_started.set()
            try:
                await second_release.wait()
            finally:
                cancelled.set()
        timeline.append(f"{message}-stop")
        return RunResult(status="completed", assistant_text=message)

    controller.stream_handler = stream_handler
    spawned = await _call(
        session,
        turn,
        "spawn_agent",
        {"message": "first", "task_name": "first"},
        {},
    )
    agent_id = _data(spawned)["agent_id"]
    await first_started.wait()

    queued = await _call(
        session,
        turn,
        "send_input",
        {"target": agent_id, "message": "second"},
        {},
    )
    await asyncio.sleep(0)
    assert not second_started.is_set()

    first_release.set()
    await second_started.wait()
    redirected = await _call(
        session,
        turn,
        "send_input",
        {"target": agent_id, "message": "third", "interrupt": True},
        {},
    )
    waited = await _call(
        session,
        turn,
        "wait_agent",
        {"targets": [agent_id]},
        {},
    )

    assert _data(queued)["submission_id"]
    assert _data(redirected)["submission_id"]
    assert cancelled.is_set()
    assert _data(waited)["status"] == {
        agent_id: {"completed": "third"},
    }
    assert timeline == [
        "first-start",
        "first-stop",
        "second-start",
        "third-start",
        "third-stop",
    ]

    await runtime.shutdown()


@pytest.mark.anyio
async def test_agent_tools_reject_cross_root_and_self_blocking_calls() -> None:
    runtime = SubagentRuntime(_Controller())
    session = _session(runtime)
    first = _root_turn()
    second = _root_turn()

    spawned = await _call(
        session,
        first,
        "spawn_agent",
        {"message": "task", "task_name": "task"},
        {},
    )
    agent_id = _data(spawned)["agent_id"]
    snapshot = await runtime.get(first.sid, agent_id)
    child_turn = TurnContext.create(
        agent=snapshot.context,
        cid=snapshot.thread.cid,
        sid=snapshot.thread.sid,
        mode=snapshot.thread.mode,
        source="subagent",
        pref_config={},
        cwd=snapshot.thread.cwd,
        permissions=snapshot.thread.permissions,
    )

    cross_root = await _call(
        session,
        second,
        "send_input",
        {"target": agent_id, "message": "wrong root"},
        {},
    )
    self_wait = await _call(
        session,
        child_turn,
        "wait_agent",
        {"targets": [agent_id]},
        {},
    )
    self_close = await _call(
        session,
        child_turn,
        "close_agent",
        {"target": agent_id},
        {},
    )

    assert cross_root.isError
    assert "root session not found" in _data(cross_root)["error"]
    assert self_wait.isError
    assert "cannot wait for its own" in _data(self_wait)["error"]
    assert self_close.isError
    assert "cannot close itself" in _data(self_close)["error"]

    await runtime.shutdown()
