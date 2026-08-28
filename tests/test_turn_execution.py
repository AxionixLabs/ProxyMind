# -*- coding: utf-8 -*-

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock
)

import pytest

from mind_app.runtime.turns.result import RunResult
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext
)
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope,
)
from mind_app.runtime.support.conversation import ConversationTurn
from mind_app.runtime.turns import root as root_turns
from mind_app.runtime.turns.executor import (
    TurnExecution,
    build_turn_input_payload,
    execute_turn,
    resolve_turn_hook_scope,
)
from mind_app.runtime.turns.event_reporting import EventReportRuntimeOwner
from mind_app.history.transcript import (
    ConversationTranscriptStore,
    TranscriptReader,
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
    def __init__(self, *, event_reporting=None) -> None:
        self.sessions = []
        self.event_reporting = event_reporting

    async def with_mcp_session(self, pref_config, function):
        self.sessions.append(pref_config)
        return await function(
            "session",
            [{"name": "tool", "meta": {"domain": "coding"}}],
        )

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


class _ReportPool(object):
    def __init__(self, report: _Report) -> None:
        self.report = report
        self.acquired = []
        self.closed = []

    async def acquire(self, cid, sid):
        self.acquired.append((cid, sid))
        return self.report

    async def close_session(self, cid, sid, *, drain=True) -> None:
        self.closed.append((cid, sid, drain))


def _event_reporting(
    report: _Report,
    *,
    pool: _ReportPool | None = None,
) -> EventReportRuntimeOwner:
    return EventReportRuntimeOwner(
        pool=pool,
        report_factory=lambda _cid, _sid: report,
    )


def _empty_hook_scope(context: TurnContext) -> HookExecutionScope:
    return HookExecutionScope(
        context=HookExecutionContext.from_turn(context),
        dispatcher=HookRuntime.empty(),
    )


def _child_execution() -> TurnExecution:
    agent = AgentContext.root("sid_root").child(
        "explore",
        "inspect",
        agent_id="agent_child",
    )
    context = TurnContext.create(
        agent=agent,
        cid="cid_child",
        sid="sid_child",
        source="subagent",
        pref_config={"primary": {"model": "test-model"}},
        cwd=".",
        permissions=preset_permissions("auto"),
        turn_id="turn_child",
    )
    return TurnExecution(
        context=context,
        message="inspect workspace",
        hook_scope=_empty_hook_scope(context),
        metadata={"origin": "test"},
    )


def _root_execution() -> TurnExecution:
    child = _child_execution()
    context = TurnContext.create(
        agent=AgentContext.root(child.context.sid),
        cid=child.context.cid,
        sid=child.context.sid,
        source="tui",
        pref_config={},
        cwd=child.context.cwd,
        permissions=child.context.permissions,
        turn_id=child.context.turn_id,
    )
    return TurnExecution(
        context=context,
        message=child.message,
        hook_scope=_empty_hook_scope(context),
        metadata=child.metadata,
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
            hook_scope=execution.hook_scope,
            metadata={"sid": "sid_other"},
        )


def test_turn_execution_allows_empty_text_but_requires_string() -> None:
    prepared = _child_execution()

    empty = TurnExecution(
        context=prepared.context,
        message="",
        hook_scope=prepared.hook_scope,
    )

    assert empty.message == ""

    with pytest.raises(TypeError, match="message must be a string"):
        TurnExecution(
            context=prepared.context,
            message=None,
            hook_scope=prepared.hook_scope,
        )


def test_turn_execution_normalizes_additional_context() -> None:
    prepared = _child_execution()

    execution = TurnExecution(
        context=prepared.context,
        message=prepared.message,
        hook_scope=prepared.hook_scope,
        additional_context=[" first ", "", "second"],
    )

    assert execution.additional_context == ("first", "second")

    with pytest.raises(TypeError, match="entries must be strings"):
        TurnExecution(
            context=prepared.context,
            message=prepared.message,
            hook_scope=prepared.hook_scope,
            additional_context=["valid", 1],
        )


def test_turn_execution_rejects_hook_scope_from_another_turn() -> None:
    prepared = _child_execution()
    other_context = TurnContext.create(
        agent=prepared.context.agent,
        cid=prepared.context.cid,
        sid=prepared.context.sid,
        source=prepared.context.source,
        pref_config={},
        cwd=prepared.context.cwd,
        permissions=prepared.context.permissions,
        turn_id="turn_other",
    )

    with pytest.raises(ValueError, match="does not belong to hook scope"):
        TurnExecution(
            context=prepared.context,
            message=prepared.message,
            hook_scope=_empty_hook_scope(other_context),
        )


@pytest.mark.anyio
async def test_execute_turn_does_not_require_root_conversation_or_frontend() -> None:
    mind = _ExecutionController()
    execution = _root_execution()
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
        [{"name": "tool", "meta": {"domain": "coding"}}],
        report,
    )]
    assert report.opened == 0
    assert report.closed == []
    assert not hasattr(mind, "conversation")
    assert not hasattr(mind, "frontend")


@pytest.mark.anyio
async def test_execute_turn_applies_explicit_tool_filter_policy() -> None:
    mind = _ExecutionController()
    received = []

    async def with_mcp_session(_pref_config, function):
        return await function("session", [
            {
                "name": "shell_command",
                "meta": {"client_builtin": True, "domain": "coding"},
            },
            {"name": "device_info", "meta": {"domain": "device"}},
            {
                "name": "nexus_http_request",
                "meta": {"domain": "bench", "class": "nexus"},
            },
            {
                "name": "plan_steps",
                "meta": {
                    "client_builtin": True,
                    "domain": "client",
                    "class": "loop",
                },
            },
            {
                "name": "spawn_agent",
                "meta": {
                    "client_builtin": True,
                    "domain": "client",
                    "class": "agent",
                },
            },
            {"name": "external_tool", "meta": {"external": True}},
        ])

    async def operation(_prepared, _session, tools, _event_report):
        received.extend(tools)
        return RunResult(status="completed")

    mind.with_mcp_session = with_mcp_session

    await execute_turn(
        mind,
        {},
        _root_execution(),
        operation,
        event_report=_Report(),
        tool_filter_mode="api",
    )

    assert received == [
        {
            "name": "shell_command",
            "meta": {"client_builtin": True, "domain": "coding"},
        },
        {
            "name": "nexus_http_request",
            "meta": {"domain": "bench", "class": "nexus"},
        },
        {
            "name": "spawn_agent",
            "meta": {
                "client_builtin": True,
                "domain": "client",
                "class": "agent",
            },
        },
        {"name": "external_tool", "meta": {"external": True}},
    ]


@pytest.mark.anyio
async def test_execute_turn_preserves_explicit_unfiltered_snapshot() -> None:
    mind = _ExecutionController()
    mind.tool_profile_for_turn = Mock(return_value="api")
    received = []
    tools = [
        {"name": "device_info", "meta": {"domain": "device"}},
        {
            "name": "nexus_http_request",
            "meta": {"domain": "bench", "class": "nexus"},
        },
    ]

    async def with_mcp_session(_pref_config, function):
        return await function("session", tools)

    async def operation(_prepared, _session, visible, _event_report):
        received.extend(visible)
        return RunResult(status="completed")

    mind.with_mcp_session = with_mcp_session

    await execute_turn(
        mind,
        {},
        _root_execution(),
        operation,
        event_report=_Report(),
        tool_filter_mode=None,
    )

    assert received == tools
    mind.tool_profile_for_turn.assert_not_called()


@pytest.mark.anyio
async def test_execute_turn_uses_linked_helix_tool_profile() -> None:
    mind = _ExecutionController()
    mind.tool_profile_for_turn = Mock(return_value="app")
    received = []

    async def with_mcp_session(_pref_config, function):
        return await function("session", [
            {"name": "device_info", "meta": {"domain": "device"}},
            {
                "name": "nexus_http_request",
                "meta": {"domain": "bench", "class": "nexus"},
            },
            {
                "name": "plan_steps",
                "meta": {
                    "client_builtin": True,
                    "domain": "client",
                    "class": "loop",
                },
            },
        ])

    async def operation(_prepared, _session, tools, _event_report):
        received.extend(tools)
        return RunResult(status="completed")

    mind.with_mcp_session = with_mcp_session

    await execute_turn(
        mind,
        {},
        _root_execution(),
        operation,
        event_report=_Report(),
    )

    assert [tool["name"] for tool in received] == [
        "device_info",
        "plan_steps",
    ]
    mind.tool_profile_for_turn.assert_called_once_with()


@pytest.mark.anyio
async def test_concurrent_turn_executions_keep_contexts_isolated() -> None:
    mind = _ExecutionController()
    first = _child_execution()
    second_context = TurnContext.create(
        agent=AgentContext.root("sid_root").child(
            "review",
            "review",
            agent_id="agent_review",
        ),
        cid="cid_review",
        sid="sid_review",
        source="subagent",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
        turn_id="turn_review",
    )
    second = TurnExecution(
        context=second_context,
        message="review workspace",
        hook_scope=_empty_hook_scope(second_context),
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
async def test_execute_turn_opens_and_closes_owned_report() -> None:
    report = _Report()

    async def operation(*_args):
        return RunResult(status="completed")

    result = await execute_turn(
        _ExecutionController(event_reporting=_event_reporting(report)),
        {},
        _child_execution(),
        operation,
    )

    assert result.status == "completed"
    assert report.opened == 1
    assert report.closed == [True]


@pytest.mark.anyio
async def test_execute_turn_reuses_session_report_without_turn_close() -> None:
    report = _Report()
    pool = _ReportPool(report)
    mind = _ExecutionController(
        event_reporting=_event_reporting(report, pool=pool)
    )

    async def operation(*_args):
        return RunResult(status="completed")

    execution = _root_execution()
    await execute_turn(mind, {}, execution, operation)
    await execute_turn(mind, {}, execution, operation)

    assert pool.acquired == [
        ("cid_child", "sid_child"),
        ("cid_child", "sid_child"),
    ]
    assert pool.closed == []
    assert report.opened == 0
    assert report.closed == []


@pytest.mark.anyio
async def test_execute_turn_discards_pooled_report_after_cancellation() -> None:
    report = _Report()
    pool = _ReportPool(report)
    mind = _ExecutionController(
        event_reporting=_event_reporting(report, pool=pool)
    )

    async def operation(*_args):
        raise asyncio.CancelledError()

    execution = _root_execution()
    with pytest.raises(asyncio.CancelledError):
        await execute_turn(mind, {}, execution, operation)

    assert pool.closed == [("cid_child", "sid_child", False)]


@pytest.mark.anyio
async def test_execute_turn_keeps_child_reports_turn_scoped() -> None:
    report = _Report()
    pool = _ReportPool(_Report())
    mind = _ExecutionController(
        event_reporting=_event_reporting(report, pool=pool)
    )

    async def operation(*_args):
        return RunResult(status="completed")

    await execute_turn(mind, {}, _child_execution(), operation)

    assert pool.acquired == []
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
    failure,
    expected_drain,
) -> None:
    report = _Report()

    async def operation(*_args):
        raise failure

    with pytest.raises(type(failure)):
        await execute_turn(
            _ExecutionController(event_reporting=_event_reporting(report)),
            {},
            _child_execution(),
            operation,
        )

    assert report.opened == 1
    assert report.closed == [expected_drain]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "terminal_event", "terminal_status"),
    [
        (RuntimeError("session failed"), "turn.failed", "failed"),
        (asyncio.CancelledError(), "turn.interrupted", "interrupted"),
    ],
)
async def test_execute_turn_records_input_when_session_setup_stops(
    tmp_path,
    failure,
    terminal_event,
    terminal_status,
) -> None:
    path = tmp_path / "session.jsonl"
    prepared = _root_execution()
    context = replace(
        prepared.context,
        transcript_path=str(path),
        session_started=True,
        session_start_reason="initial",
    )
    execution = TurnExecution(
        context=context,
        message="inspect workspace",
        hook_scope=_empty_hook_scope(context),
        input_payload=build_turn_input_payload(
            "inspect workspace",
            attachments=[{"filename": "screen.png", "kind": "image"}],
            extras={"selection": {"x": 10, "y": 20}},
        ),
    )
    operation_calls = []

    class Controller(_ExecutionController):
        transcripts = ConversationTranscriptStore

        async def with_mcp_session(self, _pref_config, _function):
            raise failure

    async def operation(*args):
        operation_calls.append(args)
        return RunResult(status="completed")

    with pytest.raises(type(failure)):
        await execute_turn(
            Controller(),
            {},
            execution,
            operation,
            event_report=_Report(),
        )

    entries = TranscriptReader(path).read()

    assert not operation_calls
    assert [entry.event for entry in entries] == [
        "session.started",
        "turn.started",
        "message.created",
        terminal_event,
    ]
    assert entries[2].payload == {
        "content": "inspect workspace",
        "attachments": [{"filename": "screen.png", "kind": "image"}],
        "extras": {"selection": {"x": 10, "y": 20}},
    }
    assert entries[3].payload["status"] == terminal_status


@pytest.mark.anyio
async def test_root_calling_composes_conversation_and_terminal_lifecycle(
    monkeypatch,
) -> None:
    permissions = preset_permissions("auto")
    runtime = SimpleNamespace(
        begin_terminal_progress=Mock(),
        end_terminal_progress=Mock(),
    )
    report = _Report()
    captured = []
    resolved_scopes = []

    async def stream_turn(*_args, **kwargs):
        captured.append(kwargs)
        return RunResult(status="completed", assistant_text="done")

    monkeypatch.setattr(root_turns, "stream_turn", stream_turn)

    async def with_mcp_session(_pref_config, function):
        return await function("session", [])

    async def await_cleanup(awaitable) -> None:
        await awaitable

    def hook_scope(context):
        scope = HookExecutionScope(
            context=context,
            dispatcher=HookRuntime.empty(),
        )
        resolved_scopes.append(scope)
        return scope

    mind = SimpleNamespace(
        permissions=permissions,
        history_workspace="D:/workspace",
        report=SimpleNamespace(output_record_path="D:/logs/output.log"),
        transcripts=SimpleNamespace(
            path_for_session=lambda _sid: "D:/sessions/session.jsonl",
        ),
        begin_conversation_turn=AsyncMock(return_value=ConversationTurn(
            cid="cid_root",
            sid="sid_root",
            turn_index=1,
            session_started=True,
            start_reason="calling",
            additional_context=("queued context",),
            system_message="queued system",
        )),
        with_mcp_session=with_mcp_session,
        await_cleanup=await_cleanup,
        start_anim=AsyncMock(),
        stop_anim=AsyncMock(),
        animate=False,
        frontend=SimpleNamespace(runtime=runtime),
        hook_scope=Mock(side_effect=hook_scope),
    )

    result = await root_turns.run_root_turn(
        mind,
        {"primary": {"model": "test-model"}},
        message="hello",
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
    streamed_execution = captured[0]["turn_execution"]
    context = streamed_execution.context
    assert context.agent.agent_id == "root"
    assert context.sid == "sid_root"
    assert context.output_record_path == "D:/logs/output.log"
    assert context.transcript_path == "D:/sessions/session.jsonl"
    assert streamed_execution.hook_scope is resolved_scopes[0]
    mind.hook_scope.assert_called_once_with(
        HookExecutionContext.from_turn(context)
    )
    assert dict(streamed_execution.metadata) == {
        "origin": "test",
        "cid": "cid_root",
        "sid": "sid_root",
    }
    assert streamed_execution.additional_context == ("queued context",)
    assert streamed_execution.system_message == "queued system"
    assert "message" not in captured[0]
    assert "metadata" not in captured[0]
    assert "permissions" not in captured[0]
    assert "turn_context" not in captured[0]
    assert "hook_scope" not in captured[0]
    assert captured[0]["ev_report"] is report
    assert report.closed == []
    runtime.begin_terminal_progress.assert_called_once_with()
    runtime.end_terminal_progress.assert_called_once_with()
    mind.start_anim.assert_awaited_once_with()
    mind.stop_anim.assert_awaited_once_with("wait")


def test_turn_hook_scope_resolution_failure_uses_empty_snapshot() -> None:
    context = _child_execution().context
    controller = SimpleNamespace(
        hook_scope=Mock(side_effect=ValueError("invalid hooks")),
    )

    scope = resolve_turn_hook_scope(controller, context)
    execution = TurnExecution(
        context=context,
        message="inspect workspace",
        hook_scope=scope,
    )

    assert execution.hook_scope is scope
    assert scope.has_matching("UserPromptSubmit") is False
    controller.hook_scope.assert_called_once_with(
        HookExecutionContext.from_turn(context)
    )
