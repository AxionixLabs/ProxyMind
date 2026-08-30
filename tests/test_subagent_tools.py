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
from mind_app.runtime.turns.result import RunResult
from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.hooks.scope import HookExecutionScope
from mind_app.runtime.subagents.runtime import SubagentRuntime
from mind_app.runtime.turns import stream as turn_stream
from mind_app.runtime.turns.event_reporting import EventReportRuntimeOwner
from agent.application import AgentSettings
from agent.application import preset_permissions
from protocol.schema.identifiers import new_cid, new_sid


@pytest.fixture(autouse=True)
def stream_operation_adapter(monkeypatch) -> None:
    """将子轮次流式操作转给测试控制器替身。"""
    async def stream_turn(controller, **kwargs):
        """调用测试控制器上的流式执行替身。"""
        return await controller.run_stream_turn(**kwargs)

    monkeypatch.setattr(turn_stream, "stream_turn", stream_turn)


class _Controller:
    def __init__(self) -> None:
        self.messages = []
        self.stream_handler = None
        self.config_session = SimpleNamespace(load=lambda: {})
        self.event_reporting = EventReportRuntimeOwner(
            report_factory=lambda _cid, _sid: _EventReport(),
        )

    def hook_scope(self, context):
        return HookExecutionScope.empty(context)

    async def with_mcp_session(self, pref_config, function):
        return await function("session", [])

    async def run_stream_turn(self, **kwargs):
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


class _EventReport:
    async def open(self) -> None:
        return None

    async def close(self, *, drain: bool = True) -> None:
        return None


def _root_turn() -> TurnContext:
    cid = new_cid()
    sid = new_sid(cid)
    return TurnContext.create(
        agent=AgentContext.root(sid),
        cid=cid,
        sid=sid,
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
        enabled=False,
    )

    enabled_tools = default_registry(
        execution_root=tmp_path,
        subagent_runtime=enabled,
    ).list_tools().tools
    enabled_names = {tool.name for tool in enabled_tools}
    disabled_names = {
        tool.name
        for tool in default_registry(
            execution_root=tmp_path,
            subagent_runtime=disabled,
        ).list_tools().tools
    }
    agent_names = {
        "spawn_agent",
        "list_agents",
        "send_message",
        "followup_task",
        "interrupt_agent",
        "resume_agent",
        "wait_agent",
        "close_agent",
    }
    assert agent_names <= enabled_names
    assert "send_input" not in enabled_names
    assert "spawn_agent" not in disabled_names

    agent_tools = [tool for tool in enabled_tools if tool.name in agent_names]
    assert len(agent_tools) == 8
    assert all(
        tool.meta["domain"] == "client" and tool.meta["class"] == "agent"
        for tool in agent_tools
    )
    assert all(
        any("\u4e00" <= char <= "\u9fff" for char in tool.description)
        for tool in agent_tools
    )
    spawn_tool = next(tool for tool in agent_tools if tool.name == "spawn_agent")
    assert spawn_tool.inputSchema["properties"]["fork_turns"]["default"] == "5"
    message_tool = next(
        tool for tool in agent_tools if tool.name == "send_message"
    )
    assert message_tool.inputSchema["properties"]["message"]["maxLength"] > 0
    for tool_name in {
        "send_message",
        "followup_task",
        "interrupt_agent",
        "resume_agent",
        "close_agent",
    }:
        tool = next(item for item in agent_tools if item.name == tool_name)
        assert "target" in tool.inputSchema["properties"]
        assert "target" in tool.inputSchema["required"]


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
        {"targets": ["/root/review"]},
        pref_config,
    )
    snapshot = await runtime.get(turn.sid, agent_id)

    assert not spawned.isError
    assert _data(waited)["status"] == {
        agent_id: {"completed": "done: first"},
    }
    assert _data(waited)["updates"][0]["kind"] == "status"
    assert _data(waited)["updates"][0]["status"] == "completed"
    assert not _data(waited)["timed_out"]
    assert snapshot.thread.config_snapshot() == {
        "primary": {"model": "child-model"},
        "routing": {"tags": ["review"]},
    }

    closed = await _call(
        session,
        turn,
        "close_agent",
        {"target": "review"},
        pref_config,
    )
    rejected = await _call(
        session,
        turn,
        "followup_task",
        {"target": "/root/review", "message": "blocked"},
        pref_config,
    )
    resumed = await _call(
        session,
        turn,
        "resume_agent",
        {"target": "review"},
        pref_config,
    )
    submitted = await _call(
        session,
        turn,
        "followup_task",
        {"target": "/root/review", "message": "second"},
        pref_config,
    )
    updates = await _call(
        session,
        turn,
        "wait_agent",
        {"targets": [agent_id]},
        pref_config,
    )
    await runtime.wait(turn.sid, [agent_id], timeout_sec=1)
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
    assert {event["kind"] for event in _data(updates)["updates"]} == {
        "queue",
        "status",
    }
    assert _data(second)["status"] == {
        agent_id: {"completed": "done: second"},
    }
    assert controller.messages == ["first", "second"]

    await runtime.shutdown()


@pytest.mark.anyio
async def test_followup_and_interrupt_tools_do_not_overlap_turns() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    session = _session(runtime)
    turn = _root_turn()
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    second_started = asyncio.Event()
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
                await asyncio.Event().wait()
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
        "followup_task",
        {"target": agent_id, "message": "second"},
        {},
    )
    await asyncio.sleep(0)
    assert not second_started.is_set()

    first_release.set()
    await second_started.wait()
    interrupted = await _call(
        session,
        turn,
        "interrupt_agent",
        {"target": agent_id},
        {},
    )
    redirected = await _call(
        session,
        turn,
        "followup_task",
        {"target": agent_id, "message": "third"},
        {},
    )
    updates = await _call(
        session,
        turn,
        "wait_agent",
        {"targets": [agent_id]},
        {},
    )
    await runtime.wait(turn.sid, [agent_id], timeout_sec=1)
    waited = await _call(
        session,
        turn,
        "wait_agent",
        {"targets": [agent_id]},
        {},
    )

    assert _data(queued)["submission_id"]
    assert _data(redirected)["submission_id"]
    assert _data(interrupted)["status"] == "interrupted"
    assert cancelled.is_set()
    assert {event["kind"] for event in _data(updates)["updates"]} == {
        "queue",
        "status",
    }
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
async def test_send_message_reaches_target_mailbox_without_new_turn() -> None:
    runtime = SubagentRuntime(_Controller())
    session = _session(runtime)
    turn = _root_turn()
    spawned = await _call(
        session,
        turn,
        "spawn_agent",
        {"message": "task", "task_name": "worker"},
        {},
    )
    agent_id = _data(spawned)["agent_id"]
    await runtime.wait(turn.sid, [agent_id], timeout_sec=1)
    snapshot = await runtime.get(turn.sid, agent_id)
    child_turn = TurnContext.create(
        agent=snapshot.context,
        cid=snapshot.thread.cid,
        sid=snapshot.thread.sid,
        source="subagent",
        pref_config={},
        cwd=snapshot.thread.cwd,
        permissions=snapshot.thread.permissions,
    )

    sent = await _call(
        session,
        turn,
        "send_message",
        {"target": "/root/worker", "message": "avoid the database layer"},
        {},
    )
    received = await _call(
        session,
        child_turn,
        "wait_agent",
        {"targets": ["root"]},
        {},
    )
    after = await runtime.get(turn.sid, agent_id)

    assert not sent.isError
    assert _data(sent)["target_agent_id"] == agent_id
    assert _data(sent)["target_task_path"] == "/root/worker"
    assert _data(sent)["delivery"] == "mailbox"
    assert _data(sent)["receipt"] is None
    assert len(_data(received)["updates"]) == 1
    update = _data(received)["updates"][0]
    assert update["kind"] == "message"
    assert update["source_agent_id"] == "root"
    assert update["recipient_agent_id"] == agent_id
    assert update["message"] == "avoid the database layer"
    assert after.turn_count == 1

    await runtime.shutdown()


@pytest.mark.anyio
async def test_list_agents_discovers_and_filters_task_paths() -> None:
    runtime = SubagentRuntime(_Controller())
    session = _session(runtime)
    turn = _root_turn()

    empty = await _call(session, turn, "list_agents", {}, {})
    first = await _call(
        session,
        turn,
        "spawn_agent",
        {"message": "first", "task_name": "foo", "agent_type": "worker"},
        {},
    )
    second = await _call(
        session,
        turn,
        "spawn_agent",
        {
            "message": "second",
            "task_name": "foobar",
            "agent_type": "reviewer",
        },
        {},
    )
    listed = await _call(session, turn, "list_agents", {}, {})
    filtered = await _call(
        session,
        turn,
        "list_agents",
        {"path_prefix": "/root/foo"},
        {},
    )

    assert _data(empty) == {"agents": []}
    assert [item["agent_id"] for item in _data(listed)["agents"]] == [
        _data(first)["agent_id"],
        _data(second)["agent_id"],
    ]
    assert len(_data(filtered)["agents"]) == 1
    filtered_agent = _data(filtered)["agents"][0]
    assert filtered_agent["agent_id"] == _data(first)["agent_id"]
    assert filtered_agent["agent_type"] == "worker"
    assert filtered_agent["task_name"] == "foo"
    assert filtered_agent["task_path"] == "/root/foo"
    assert filtered_agent["parent_agent_id"] == "root"
    assert filtered_agent["status"] in {"pending", "running", "completed"}
    assert filtered_agent["turn_count"] == 1
    assert filtered_agent["queued_count"] == 0

    await runtime.shutdown()


@pytest.mark.anyio
async def test_interrupt_agent_cancels_without_submitting_followup() -> None:
    controller = _Controller()
    runtime = SubagentRuntime(controller)
    session = _session(runtime)
    turn = _root_turn()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def stream_handler(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    controller.stream_handler = stream_handler
    spawned = await _call(
        session,
        turn,
        "spawn_agent",
        {"message": "first", "task_name": "interruptible"},
        {},
    )
    await started.wait()

    interrupted = await _call(
        session,
        turn,
        "interrupt_agent",
        {"target": "/root/interruptible"},
        {},
    )
    snapshot = await runtime.get(turn.sid, _data(spawned)["agent_id"])

    assert not interrupted.isError
    assert _data(interrupted) == {
        "agent_id": _data(spawned)["agent_id"],
        "task_path": "/root/interruptible",
        "status": "interrupted",
    }
    assert cancelled.is_set()
    assert snapshot.status == "interrupted"
    assert snapshot.turn_count == 1
    assert snapshot.queued_count == 0
    assert controller.messages == ["first"]

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
        source="subagent",
        pref_config={},
        cwd=snapshot.thread.cwd,
        permissions=snapshot.thread.permissions,
    )

    cross_root = await _call(
        session,
        second,
        "send_message",
        {"target": agent_id, "message": "wrong root"},
        {},
    )
    self_wait = await _call(
        session,
        child_turn,
        "wait_agent",
        {"targets": [snapshot.context.task_path]},
        {},
    )
    self_interrupt = await _call(
        session,
        child_turn,
        "interrupt_agent",
        {"target": snapshot.context.task_path},
        {},
    )
    self_close = await _call(
        session,
        child_turn,
        "close_agent",
        {"target": snapshot.context.task_path},
        {},
    )

    assert cross_root.isError
    assert "root session not found" in _data(cross_root)["error"]
    assert self_wait.isError
    assert "cannot wait for its own" in _data(self_wait)["error"]
    assert self_interrupt.isError
    assert "cannot interrupt its own" in _data(self_interrupt)["error"]
    assert self_close.isError
    assert "cannot close itself" in _data(self_close)["error"]

    await runtime.shutdown()
