# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from mind_app.runtime.turns.result import RunResult
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext,
)
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope,
)
from mind_app.runtime.hooks.subagent import SubagentHookEvents
from mind_app.runtime.subagents.runner import (
    MAX_SUBAGENT_STOP_CONTINUATIONS,
    SubagentRunner,
)
from mind_app.runtime.turns.executor import TurnExecution
from mind_core.hook_discovery import resolve_hook_definitions
from agent.application import preset_permissions


class _Report:
    pass


class _CommandRunner:
    def __init__(self, timeline, *, outputs=None, errors=None) -> None:
        self.timeline = timeline
        self.outputs = dict(outputs or {})
        self.errors = dict(errors or {})
        self.calls = []

    async def execute(self, definition, payload):
        self.timeline.append(definition.event)
        self.calls.append((definition, payload))
        error = self.errors.get(definition.key)
        if error is not None:
            raise error
        return SimpleNamespace(data=dict(self.outputs.get(definition.key) or {}))


class _Controller:
    def __init__(self) -> None:
        self.sessions = []

    async def with_mcp_session(self, pref_config, function):
        self.sessions.append(pref_config)
        return await function(
            "session",
            [{"name": "tool", "meta": {"domain": "coding"}}],
        )

    @staticmethod
    async def await_cleanup(awaitable) -> None:
        await awaitable


def _definitions(raw):
    return resolve_hook_definitions(
        raw,
        source_scope="user",
        source_path=Path("config.toml"),
    )


def _hook(command, *, matcher=None):
    config = {
        "hooks": [{"type": "command", "command": command}],
    }
    if matcher is not None:
        config["matcher"] = matcher
    return config


def _execution(
    *,
    agent_type: str = "explore",
    dispatcher: HookRuntime | None = None,
    session_started: bool = True,
) -> TurnExecution:
    agent = AgentContext.root("sid_root").child(
        agent_type,
        "child",
        agent_id="agent_child",
    )
    context = TurnContext.create(
        agent=agent,
        cid="cid_child",
        sid="sid_child",
        source="subagent",
        pref_config={"primary": {"model": "test-model"}},
        cwd="D:/workspace",
        permissions=preset_permissions("auto"),
        transcript_path="D:/logs/subagent.log",
        parent_transcript_path="D:/logs/root.log",
        turn_id="turn_child",
        session_started=session_started,
        session_start_reason="subagent" if session_started else "",
    )
    return TurnExecution(
        context=context,
        message="inspect workspace",
        hook_scope=HookExecutionScope(
            context=HookExecutionContext.from_turn(context),
            dispatcher=dispatcher or HookRuntime.empty(),
        ),
    )


def _root_execution() -> TurnExecution:
    context = TurnContext.create(
        agent=AgentContext.root("sid_root"),
        cid="cid_root",
        sid="sid_root",
        source="test",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
    )
    return TurnExecution(
        context=context,
        message="root task",
        hook_scope=HookExecutionScope(
            context=HookExecutionContext.from_turn(context),
            dispatcher=HookRuntime.empty(),
        ),
    )


def _runtime(raw, timeline, *, outputs=None, errors=None):
    definitions = _definitions(raw)
    runner = _CommandRunner(timeline, outputs=outputs, errors=errors)
    return definitions, runner, HookRuntime(
        definitions,
        command_runner=runner,
    )


@pytest.mark.anyio
async def test_subagent_runner_dispatches_fixed_lifecycle_scope() -> None:
    timeline = []
    definitions, command_runner, runtime = _runtime({
        "SubagentStart": [_hook("start", matcher="explore")],
        "SubagentStop": [_hook("stop", matcher="explore")],
    }, timeline)
    execution = _execution(dispatcher=runtime)
    controller = _Controller()

    async def operation(prepared, session, tools, report):
        timeline.append("operation")
        assert prepared is execution
        assert prepared.hook_scope.dispatcher is runtime
        assert session == "session"
        assert tools == [{"name": "tool", "meta": {"domain": "coding"}}]
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
    assert controller.sessions == [{"primary": {"model": "test-model"}}]

    start_payload = command_runner.calls[0][1]
    assert command_runner.calls[0][0] == definitions[0]
    assert start_payload == {
        "session_id": "sid_child",
        "transcript_path": "D:/logs/subagent.log",
        "cwd": "D:/workspace",
        "hook_event_name": "SubagentStart",
        "model": "test-model",
        "permission_mode": "default",
        "turn_id": "turn_child",
        "agent_id": "agent_child",
        "agent_type": "explore",
    }

    stop_payload = command_runner.calls[1][1]
    assert command_runner.calls[1][0] == definitions[1]
    assert stop_payload == {
        "agent_transcript_path": "D:/logs/subagent.log",
        "stop_hook_active": False,
        "last_assistant_message": "done",
        "session_id": "sid_child",
        "transcript_path": "D:/logs/root.log",
        "cwd": "D:/workspace",
        "hook_event_name": "SubagentStop",
        "model": "test-model",
        "permission_mode": "default",
        "turn_id": "turn_child",
        "agent_id": "agent_child",
        "agent_type": "explore",
    }


@pytest.mark.anyio
async def test_subagent_runner_skips_non_matching_hooks() -> None:
    timeline = []
    _definitions_value, command_runner, runtime = _runtime({
        "SubagentStart": [_hook("start", matcher="review")],
        "SubagentStop": [_hook("stop", matcher="review")],
    }, timeline)
    execution = _execution(dispatcher=runtime)
    controller = _Controller()

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
async def test_subagent_start_injects_ordered_context_only_for_first_turn() -> None:
    timeline = []
    definitions = _definitions({
        "SubagentStart": [
            _hook("first"),
            _hook("second"),
        ],
        "SubagentStop": [_hook("stop")],
    })
    command_runner = _CommandRunner(timeline, outputs={
        definitions[0].key: {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": "inspect security boundaries",
            },
        },
        definitions[1].key: {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": "check cancellation paths",
            },
        },
    })
    runtime = HookRuntime(definitions, command_runner=command_runner)
    prepared_turns = []

    async def operation(prepared, *_args):
        prepared_turns.append(prepared)
        return RunResult(status="completed")

    await SubagentRunner(_Controller()).run(
        {},
        _execution(dispatcher=runtime),
        operation,
        event_report=_Report(),
    )
    await SubagentRunner(_Controller()).run(
        {},
        _execution(dispatcher=runtime, session_started=False),
        operation,
        event_report=_Report(),
    )

    assert prepared_turns[0].additional_context == (
        "inspect security boundaries",
        "check cancellation paths",
    )
    assert prepared_turns[1].additional_context == ()
    assert [call[0].event for call in command_runner.calls] == [
        "SubagentStart",
        "SubagentStart",
        "SubagentStop",
        "SubagentStop",
    ]


@pytest.mark.anyio
async def test_subagent_stop_continue_false_overrides_block_decisions() -> None:
    timeline = []
    definitions = _definitions({
        "SubagentStop": [
            _hook("continue-first"),
            _hook("veto"),
            _hook("continue-last"),
        ],
    })
    command_runner = _CommandRunner(timeline, outputs={
        definitions[0].key: {
            "decision": "block",
            "reason": "first continuation",
        },
        definitions[1].key: {
            "continue": False,
            "stopReason": "finish now",
        },
        definitions[2].key: {
            "decision": "block",
            "reason": "second continuation",
        },
    })
    execution = _execution(dispatcher=HookRuntime(
        definitions,
        command_runner=command_runner,
    ))

    decision = await SubagentHookEvents(execution.hook_scope).stop(
        outcome="completed",
        last_assistant_message="done",
        continuation_count=1,
    )

    assert not decision.should_continue
    assert decision.hook_keys == (definitions[1].key,)
    assert len(command_runner.calls) == 3
    payload = command_runner.calls[0][1]
    assert payload["stop_hook_active"] is True
    assert payload["last_assistant_message"] == "done"
    assert payload["agent_transcript_path"] == "D:/logs/subagent.log"


@pytest.mark.anyio
async def test_subagent_stop_continuation_has_hard_limit() -> None:
    timeline = []
    definitions = _definitions({
        "SubagentStart": [_hook("start")],
        "SubagentStop": [_hook("continue")],
    })
    command_runner = _CommandRunner(timeline, outputs={
        definitions[0].key: {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": "initial context",
            },
        },
        definitions[1].key: {
            "decision": "block",
            "reason": "run another focused pass",
            "systemMessage": "Continue only with missing checks.",
        },
    })
    runtime = HookRuntime(definitions, command_runner=command_runner)
    prepared_turns = []

    async def operation(prepared, *_args):
        prepared_turns.append(prepared)
        return RunResult(
            status="failed" if len(prepared_turns) == 1 else "completed",
            assistant_text=f"reply {len(prepared_turns)}",
            additional_context=(
                ("prompt policy context",)
                if len(prepared_turns) == 1
                else ()
            ),
        )

    result = await SubagentRunner(_Controller()).run(
        {},
        _execution(dispatcher=runtime),
        operation,
        event_report=_Report(),
    )

    assert result.assistant_text == "reply 4"
    assert len(prepared_turns) == MAX_SUBAGENT_STOP_CONTINUATIONS + 1
    assert len({turn.context.turn_id for turn in prepared_turns}) == 4
    assert [turn.message for turn in prepared_turns] == [
        "inspect workspace",
        "run another focused pass",
        "run another focused pass",
        "run another focused pass",
    ]
    assert prepared_turns[0].additional_context == ("initial context",)
    assert [
        turn.additional_context
        for turn in prepared_turns[1:]
    ] == [
        ("prompt policy context",),
        (),
        (),
    ]
    assert [turn.system_message for turn in prepared_turns[1:]] == ["", "", ""]
    assert [turn.context.session_started for turn in prepared_turns] == [
        True,
        False,
        False,
        False,
    ]

    stop_payloads = [
        payload
        for definition, payload in command_runner.calls
        if definition.event == "SubagentStop"
    ]
    assert [payload["stop_hook_active"] for payload in stop_payloads] == [
        False,
        True,
        True,
        True,
    ]
    assert [payload["last_assistant_message"] for payload in stop_payloads] == [
        "reply 1",
        "reply 2",
        "reply 3",
        "reply 4",
    ]


@pytest.mark.anyio
async def test_subagent_hook_command_failures_do_not_replace_result() -> None:
    timeline = []
    definitions = _definitions({
        "SubagentStart": [_hook("start")],
        "SubagentStop": [_hook("stop")],
    })
    command_runner = _CommandRunner(
        timeline,
        errors={definition.key: RuntimeError("hook failed") for definition in definitions},
    )
    runtime = HookRuntime(definitions, command_runner=command_runner)
    execution = _execution(dispatcher=runtime)
    controller = _Controller()

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
        "SubagentStop": [_hook("stop")],
    }, timeline)
    execution = _execution(dispatcher=runtime)
    controller = _Controller()

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
    assert payload["hook_event_name"] == "SubagentStop"
    assert payload["stop_hook_active"] is False
    assert payload["last_assistant_message"] is None
    assert payload["agent_id"] == "agent_child"
    assert payload["agent_type"] == "explore"


@pytest.mark.anyio
async def test_subagent_runner_preserves_operation_failure() -> None:
    timeline = []
    _definitions_value, command_runner, runtime = _runtime({
        "SubagentStart": [_hook("start")],
        "SubagentStop": [_hook("stop")],
    }, timeline)
    execution = _execution(dispatcher=runtime)
    controller = _Controller()

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
    assert payload["hook_event_name"] == "SubagentStop"
    assert payload["stop_hook_active"] is False
    assert payload["last_assistant_message"] is None


@pytest.mark.anyio
async def test_subagent_runner_dispatches_stop_after_cancellation() -> None:
    timeline = []
    _definitions_value, command_runner, runtime = _runtime({
        "SubagentStart": [_hook("start")],
        "SubagentStop": [_hook("stop")],
    }, timeline)
    execution = _execution(dispatcher=runtime)
    controller = _Controller()

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
    assert payload["hook_event_name"] == "SubagentStop"
    assert payload["stop_hook_active"] is False
    assert payload["last_assistant_message"] is None


@pytest.mark.anyio
async def test_subagent_runner_rejects_root_turn_before_side_effects() -> None:
    controller = _Controller()
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
    assert controller.sessions == []


@pytest.mark.anyio
async def test_subagent_runner_rejects_empty_task_before_side_effects() -> None:
    controller = _Controller()
    prepared = _execution()
    execution = TurnExecution(
        context=prepared.context,
        message="",
        hook_scope=prepared.hook_scope,
    )
    operation_calls = []

    async def operation(*_args):
        operation_calls.append(True)
        return RunResult(status="completed")

    with pytest.raises(ValueError, match="task is required"):
        await SubagentRunner(controller).run(
            {},
            execution,
            operation,
            event_report=_Report(),
        )

    assert operation_calls == []
    assert controller.sessions == []
