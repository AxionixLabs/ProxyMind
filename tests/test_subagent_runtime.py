# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from mind_app.modes.result import RunResult
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext,
)
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import HookExecutionScope
from mind_app.runtime.subagents.runner import SubagentRunner
from mind_app.runtime.turns.executor import TurnExecution
from mind_core.hooks import resolve_hook_definitions
from mind_core.permissions import preset_permissions


class _Report:
    pass


class _CommandRunner:
    def __init__(self, timeline, *, errors=None) -> None:
        self.timeline = timeline
        self.errors = dict(errors or {})
        self.calls = []

    async def execute(self, definition, payload):
        self.timeline.append(definition.event)
        self.calls.append((definition, payload))
        error = self.errors.get(definition.key)
        if error is not None:
            raise error
        return SimpleNamespace(data={})


class _Controller:
    def __init__(self, dispatcher) -> None:
        self.dispatcher = dispatcher
        self.scope_contexts = []
        self.sessions = []

    def hook_scope(self, context):
        self.scope_contexts.append(context)
        return HookExecutionScope(
            context=context,
            dispatcher=self.dispatcher,
        )

    async def with_mcp_session(self, pref_config, function):
        self.sessions.append(pref_config)
        return await function("session", [{"name": "tool"}])

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


def _definitions(raw):
    return resolve_hook_definitions(
        raw,
        source_scope="user",
        source_path=Path("hooks.toml"),
    )


def _execution(*, agent_type: str = "explore") -> TurnExecution:
    agent = AgentContext.root("sid_root").child(
        agent_type,
        agent_id="agent_child",
    )
    context = TurnContext.create(
        agent=agent,
        cid="cid_child",
        sid="sid_child",
        mode="xtra",
        source="subagent",
        pref_config={"primary": {"model": "test-model"}},
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
        turn_id="turn_child",
    )
    return TurnExecution(context=context, message="inspect workspace")


def _root_execution() -> TurnExecution:
    context = TurnContext.create(
        agent=AgentContext.root("sid_root"),
        cid="cid_root",
        sid="sid_root",
        mode="xtra",
        source="test",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
    )
    return TurnExecution(context=context, message="root task")


def _runtime(raw, timeline, *, errors=None):
    definitions = _definitions(raw)
    runner = _CommandRunner(timeline, errors=errors)
    return definitions, runner, HookRuntime(
        definitions,
        command_runner=runner,
    )


@pytest.mark.anyio
async def test_subagent_runner_dispatches_fixed_lifecycle_scope() -> None:
    timeline = []
    definitions, command_runner, runtime = _runtime({
        "SubagentStart": [{"command": "start", "matcher": "explore"}],
        "SubagentStop": [{"command": "stop", "matcher": "explore"}],
    }, timeline)
    execution = _execution()
    controller = _Controller(runtime)

    async def operation(prepared, hook_scope, session, tools, report):
        timeline.append("operation")
        assert prepared is execution
        assert hook_scope.context is controller.scope_contexts[0]
        assert hook_scope.dispatcher is runtime
        assert session == "session"
        assert tools == [{"name": "tool"}]
        assert isinstance(report, _Report)
        return RunResult(
            status="completed",
            assistant_text="done",
            usage={"output_tokens": 3},
        )

    result = await SubagentRunner(controller).run(
        {"primary": {"model": "test-model"}},
        execution,
        operation,
        event_report=_Report(),
    )

    assert result.status == "completed"
    assert timeline == ["SubagentStart", "operation", "SubagentStop"]
    assert len(controller.scope_contexts) == 1
    assert controller.sessions == [{"primary": {"model": "test-model"}}]

    start_payload = command_runner.calls[0][1]
    assert command_runner.calls[0][0] == definitions[0]
    assert start_payload["task"] == "inspect workspace"
    assert start_payload["session_id"] == "sid_child"
    assert start_payload["root_session_id"] == "sid_root"
    assert start_payload["conversation_id"] == "cid_child"
    assert start_payload["agent_id"] == "agent_child"
    assert start_payload["agent_type"] == "explore"
    assert start_payload["agent_depth"] == 1
    assert start_payload["parent_agent_id"] == "root"

    stop_payload = command_runner.calls[1][1]
    assert command_runner.calls[1][0] == definitions[1]
    assert stop_payload["outcome"] == "completed"
    assert stop_payload["error"] == ""
    assert stop_payload["usage"] == {"output_tokens": 3}


@pytest.mark.anyio
async def test_subagent_runner_skips_non_matching_hooks() -> None:
    timeline = []
    _definitions_value, command_runner, runtime = _runtime({
        "SubagentStart": [{"command": "start", "matcher": "review"}],
        "SubagentStop": [{"command": "stop", "matcher": "review"}],
    }, timeline)
    execution = _execution()
    controller = _Controller(runtime)

    async def operation(*_args):
        timeline.append("operation")
        return RunResult(status="completed")

    result = await SubagentRunner(controller).run(
        {},
        execution,
        operation,
        event_report=_Report(),
    )

    assert result.status == "completed"
    assert timeline == ["operation"]
    assert command_runner.calls == []


@pytest.mark.anyio
async def test_subagent_hook_command_failures_do_not_replace_result() -> None:
    timeline = []
    definitions = _definitions({
        "SubagentStart": [{"command": "start"}],
        "SubagentStop": [{"command": "stop"}],
    })
    command_runner = _CommandRunner(
        timeline,
        errors={definition.key: RuntimeError("hook failed") for definition in definitions},
    )
    runtime = HookRuntime(definitions, command_runner=command_runner)
    execution = _execution()
    controller = _Controller(runtime)

    async def operation(*_args):
        timeline.append("operation")
        return RunResult(status="completed", assistant_text="done")

    result = await SubagentRunner(controller).run(
        {},
        execution,
        operation,
        event_report=_Report(),
    )

    assert result.assistant_text == "done"
    assert timeline == ["SubagentStart", "operation", "SubagentStop"]


@pytest.mark.anyio
async def test_subagent_runner_reports_failed_result() -> None:
    timeline = []
    _definitions_value, command_runner, runtime = _runtime({
        "SubagentStop": [{"command": "stop"}],
    }, timeline)
    execution = _execution()
    controller = _Controller(runtime)

    async def operation(*_args):
        return RunResult(
            status="failed",
            error="request failed",
            usage={"input_tokens": 5},
        )

    result = await SubagentRunner(controller).run(
        {},
        execution,
        operation,
        event_report=_Report(),
    )

    assert result.status == "failed"
    payload = command_runner.calls[0][1]
    assert payload["outcome"] == "failed"
    assert payload["error"] == "request failed"
    assert payload["usage"] == {"input_tokens": 5}


@pytest.mark.anyio
async def test_subagent_runner_preserves_operation_failure() -> None:
    timeline = []
    _definitions_value, command_runner, runtime = _runtime({
        "SubagentStart": [{"command": "start"}],
        "SubagentStop": [{"command": "stop"}],
    }, timeline)
    execution = _execution()
    controller = _Controller(runtime)

    async def operation(*_args):
        timeline.append("operation")
        raise RuntimeError("model failed")

    with pytest.raises(RuntimeError, match="model failed"):
        await SubagentRunner(controller).run(
            {},
            execution,
            operation,
            event_report=_Report(),
        )

    assert timeline == ["SubagentStart", "operation", "SubagentStop"]
    payload = command_runner.calls[-1][1]
    assert payload["outcome"] == "failed"
    assert payload["error"] == "RuntimeError: model failed"


@pytest.mark.anyio
async def test_subagent_runner_dispatches_stop_after_cancellation() -> None:
    timeline = []
    _definitions_value, command_runner, runtime = _runtime({
        "SubagentStart": [{"command": "start"}],
        "SubagentStop": [{"command": "stop"}],
    }, timeline)
    execution = _execution()
    controller = _Controller(runtime)

    async def operation(*_args):
        timeline.append("operation")
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await SubagentRunner(controller).run(
            {},
            execution,
            operation,
            event_report=_Report(),
        )

    assert timeline == ["SubagentStart", "operation", "SubagentStop"]
    payload = command_runner.calls[-1][1]
    assert payload["outcome"] == "interrupted"
    assert payload["error"] == "subagent execution cancelled"


@pytest.mark.anyio
async def test_subagent_runner_rejects_root_turn_before_side_effects() -> None:
    controller = _Controller(HookRuntime.empty())
    operation_calls = []

    async def operation(*_args):
        operation_calls.append(True)
        return RunResult(status="completed")

    with pytest.raises(ValueError, match="child agent context"):
        await SubagentRunner(controller).run(
            {},
            _root_execution(),
            operation,
            event_report=_Report(),
        )

    assert operation_calls == []
    assert controller.scope_contexts == []
    assert controller.sessions == []
