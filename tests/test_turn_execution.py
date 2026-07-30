# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock
)

import pytest

from mind_app.modes.result import RunResult
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext
)
from mind_app.runtime.support.calling import calling
from mind_app.runtime.support.conversation import ConversationTurn
from mind_app.runtime.turns import executor as turn_executor
from mind_app.runtime.turns.executor import (
    TurnExecution,
    execute_turn
)
from mind_core.permissions import preset_permissions


class _Report(object):
    def __init__(self) -> None:
        self.opened = 0
        self.closed = []

    async def open(self) -> None:
        self.opened += 1

    async def close(self, *, drain: bool = True) -> None:
        self.closed.append(drain)


class _ExecutionController(object):
    def __init__(self) -> None:
        self.sessions = []

    async def with_mcp_session(self, pref_config, function):
        self.sessions.append(pref_config)
        return await function("session", [{"name": "tool"}])

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


def _child_execution() -> TurnExecution:
    agent = AgentContext.root("sid_root").child(
        "explore",
        agent_id="agent_child",
    )
    context = TurnContext.create(
        agent=agent,
        cid="cid_child",
        sid="sid_child",
        mode="xtra",
        source="subagent",
        pref_config={"primary": {"model": "test-model"}},
        cwd=".",
        permissions=preset_permissions("auto"),
        turn_id="turn_child",
    )
    return TurnExecution(
        context=context,
        message="inspect workspace",
        metadata={"origin": "test"},
    )


def test_turn_execution_fixes_context_session_metadata() -> None:
    execution = _child_execution()

    assert execution.metadata == {
        "origin": "test",
        "cid": "cid_child",
        "sid": "sid_child",
    }

    with pytest.raises(ValueError, match="metadata sid"):
        TurnExecution(
            context=execution.context,
            message=execution.message,
            metadata={"sid": "sid_other"},
        )


def test_turn_execution_allows_empty_text_but_requires_string() -> None:
    prepared = _child_execution()

    empty = TurnExecution(
        context=prepared.context,
        message="",
    )

    assert empty.message == ""

    with pytest.raises(TypeError, match="message must be a string"):
        TurnExecution(
            context=prepared.context,
            message=None,
        )


@pytest.mark.anyio
async def test_execute_turn_does_not_require_root_conversation_or_frontend() -> None:
    mind = _ExecutionController()
    execution = _child_execution()
    report = _Report()
    received = []

    async def operation(prepared, session, tools, event_report):
        received.append((prepared, session, tools, event_report))
        return RunResult(status="completed", assistant_text="done")

    result = await execute_turn(
        mind,
        {"primary": {"model": "test-model"}},
        execution,
        operation,
        event_report=report,
    )

    assert result.status == "completed"
    assert mind.sessions == [{"primary": {"model": "test-model"}}]
    assert received == [(
        execution,
        "session",
        [{"name": "tool"}],
        report,
    )]
    assert report.opened == 0
    assert report.closed == []
    assert not hasattr(mind, "conversation")
    assert not hasattr(mind, "frontend")


@pytest.mark.anyio
async def test_concurrent_turn_executions_keep_contexts_isolated() -> None:
    mind = _ExecutionController()
    first = _child_execution()
    second_context = TurnContext.create(
        agent=AgentContext.root("sid_root").child(
            "review",
            agent_id="agent_review",
        ),
        cid="cid_review",
        sid="sid_review",
        mode="xtra",
        source="subagent",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
        turn_id="turn_review",
    )
    second = TurnExecution(
        context=second_context,
        message="review workspace",
        metadata={"origin": "review"},
    )
    started = []
    release = asyncio.Event()
    captured = []

    async def operation(prepared, _session, _tools, _report):
        started.append(prepared.context.turn_id)
        if len(started) == 2:
            release.set()
        await release.wait()
        captured.append((
            prepared.context.agent.agent_id,
            dict(prepared.metadata),
        ))
        return RunResult(status="completed")

    results = await asyncio.gather(
        execute_turn(mind, {}, first, operation, event_report=_Report()),
        execute_turn(mind, {}, second, operation, event_report=_Report()),
    )

    assert [result.status for result in results] == ["completed", "completed"]
    assert set(agent_id for agent_id, _metadata in captured) == {
        "agent_child",
        "agent_review",
    }
    assert {metadata["sid"] for _agent_id, metadata in captured} == {
        "sid_child",
        "sid_review",
    }


@pytest.mark.anyio
async def test_execute_turn_opens_and_closes_owned_report(monkeypatch) -> None:
    report = _Report()
    monkeypatch.setattr(turn_executor, "EventReport", lambda *_args: report)

    async def operation(*_args):
        return RunResult(status="completed")

    result = await execute_turn(
        _ExecutionController(),
        {},
        _child_execution(),
        operation,
    )

    assert result.status == "completed"
    assert report.opened == 1
    assert report.closed == [True]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_drain"),
    [
        (RuntimeError("failed"), True),
        (asyncio.CancelledError(), False),
    ],
)
async def test_execute_turn_closes_owned_report_for_failure_and_cancellation(
    monkeypatch,
    failure,
    expected_drain,
) -> None:
    report = _Report()
    monkeypatch.setattr(turn_executor, "EventReport", lambda *_args: report)

    async def operation(*_args):
        raise failure

    with pytest.raises(type(failure)):
        await execute_turn(
            _ExecutionController(),
            {},
            _child_execution(),
            operation,
        )

    assert report.opened == 1
    assert report.closed == [expected_drain]


@pytest.mark.anyio
async def test_root_calling_composes_conversation_and_terminal_lifecycle() -> None:
    permissions = preset_permissions("auto")
    runtime = SimpleNamespace(
        begin_terminal_progress=Mock(),
        end_terminal_progress=Mock(),
    )
    report = _Report()
    captured = []

    async def stream_looper(*_args, **kwargs):
        captured.append(kwargs)
        return RunResult(status="completed", assistant_text="done")

    async def with_mcp_session(_pref_config, function):
        return await function("session", [])

    async def await_cleanup(awaitable) -> None:
        await awaitable

    mind = SimpleNamespace(
        permissions=permissions,
        history_workspace="D:/workspace",
        begin_conversation_turn=Mock(return_value=ConversationTurn(
            cid="cid_root",
            sid="sid_root",
            turn_index=1,
            session_started=True,
            start_reason="calling",
        )),
        stream_looper=stream_looper,
        with_mcp_session=with_mcp_session,
        await_cleanup=await_cleanup,
        start_anim=AsyncMock(),
        stop_anim=AsyncMock(),
        animate=False,
        frontend=SimpleNamespace(runtime=runtime),
    )

    result = await calling(
        mind,
        {"primary": {"model": "test-model"}},
        message="hello",
        mode="xtra",
        metadata={"origin": "test"},
        ev_report=report,
    )

    assert result.status == "completed"
    mind.begin_conversation_turn.assert_called_once_with(
        cid=None,
        sid=None,
        title="hello",
        source="calling",
    )
    context = captured[0]["turn_context"]
    assert context.agent.agent_id == "root"
    assert context.sid == "sid_root"
    assert captured[0]["metadata"] == {
        "origin": "test",
        "cid": "cid_root",
        "sid": "sid_root",
    }
    assert captured[0]["ev_report"] is report
    assert report.closed == []
    runtime.begin_terminal_progress.assert_called_once_with()
    runtime.end_terminal_progress.assert_called_once_with()
    mind.start_anim.assert_awaited_once_with("xtra")
    mind.stop_anim.assert_awaited_once_with("wait")
