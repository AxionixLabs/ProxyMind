# -*- coding: utf-8 -*-

import asyncio
import typing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock
)

import pytest

from mind_app.client_tools.planning import PLAN_STEPS_TOOL
from mind_app.approval.coordinator import ApprovalCoordinator
from mind_app.runtime.turns import stream
from mind_app.runtime.turns.result import RunResult
from mind_app.output.content import (
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    SourcesOutput,
)
from mind_app.output.session import OutputSession
from mind_app.presentation.models import ApprovalView
from mind_app.runtime.mcp import tool_runtime
from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from mind_app.runtime.turns.executor import (
    TurnExecution,
    build_turn_input_payload,
)
from mind_app.runtime.tools.client_call import (
    ClientToolCallOutcome,
    ClientToolCallResult,
)
from mind_app.runtime.tools.plan_steps import PlanExecutionReport
from mind_core.hook_discovery import resolve_hook_definitions
from mind_core.permissions import preset_permissions
from mind_nova.stream_events import TurnInputAcceptedEvent, parse_stream_event
from mind_nova.turn_inputs import TurnInput


class _OutputControl(object):
    async def open(self) -> None:
        return None

    async def stop(self, *, blink: bool = True) -> None:
        _ = blink

    async def record_hidden_output(self, text: str) -> None:
        _ = text

    def record_tool_arguments(self, *_args, **_kwargs) -> None:
        return None


class _OutputStatus(object):
    async def begin_tool_status(self) -> None:
        return None

    async def begin_custom_tool_status(self, text: str | None) -> None:
        _ = text

    async def begin_reply_wait_status(
        self,
        text: str | None = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        _ = (text, delay_sec, animate_after_sec)

    async def end_status(self, *, immediate: bool = False) -> None:
        _ = immediate


class _Sink(object):
    def __init__(self) -> None:
        self.items: list[object] = []

    async def emit(self, item: object) -> None:
        self.items.append(item)


class _TranscriptWriter(object):
    def __init__(self, entries) -> None:
        self.entries = entries

    def open(self) -> None:
        return None

    def append(self, event, *, actor=None, payload=None) -> None:
        self.entries.append({
            "event": event,
            "actor": actor,
            "payload": dict(payload or {}),
        })

    def close(self) -> None:
        return None


class _TranscriptStore(object):
    def __init__(self) -> None:
        self.entries = []

    def writer(self, *_args, **_kwargs) -> _TranscriptWriter:
        return _TranscriptWriter(self.entries)


def _output_session() -> OutputSession:
    return OutputSession(
        control=_OutputControl(),
        status=_OutputStatus(),
        content=_Sink(),
        presentation=_Sink(),
    )


def _hook(command, *, matcher=None):
    config = {
        "hooks": [{"type": "command", "command": command}],
    }
    if matcher is not None:
        config["matcher"] = matcher
    return config


def _mind(*, frontend_active: bool = True) -> SimpleNamespace:
    remembered: list[str] = []
    queued_context: list[tuple[str, ...]] = []

    async def await_cleanup(awaitable) -> None:
        await awaitable

    interaction = SimpleNamespace(
        request_approval=AsyncMock(return_value="accept"),
    )
    transcripts = _TranscriptStore()
    return SimpleNamespace(
        report=SimpleNamespace(output_record_path=""),
        transcripts=transcripts,
        frontend=SimpleNamespace(
            runtime=SimpleNamespace(active=frontend_active),
            interaction=interaction,
        ),
        approval_coordinator=ApprovalCoordinator(interaction),
        stop_anim=AsyncMock(),
        await_cleanup=await_cleanup,
        remember_last_assistant_reply=remembered.append,
        remembered=remembered,
        conversation=SimpleNamespace(
            queue_turn_context=lambda contexts: queued_context.append(
                tuple(contexts)
            ),
        ),
        queued_context=queued_context,
    )


async def _run_stream(
    monkeypatch,
    events: list[dict[str, typing.Any]],
    *,
    hooks: HookRuntime | None = None,
    hook_scope_factory: typing.Callable[
        [HookExecutionContext],
        HookExecutionScope,
    ] | None = None,
    session_started: bool = False,
    child_agent: bool = False,
    frontend_active: bool = True,
    additional_context: tuple[str, ...] = (),
    request_skills: tuple[dict[str, str], ...] | None = None,
    attachments: tuple[dict[str, typing.Any], ...] = (),
    extras: dict[str, typing.Any] | None = None,
    stream_factory: typing.Callable[
        ..., typing.AsyncIterator[typing.Any]
    ] | None = None,
    on_turn_input_event: typing.Callable[[typing.Any], typing.Any] | None = None,
) -> tuple[RunResult, SimpleNamespace]:
    if stream_factory is None:
        async def stream_chat(*_args, **_kwargs):
            for payload in events:
                yield parse_stream_event(payload)
    else:
        stream_chat = stream_factory

    monkeypatch.setattr(stream, "stream_chat", stream_chat)
    mind = _mind(frontend_active=frontend_active)
    output_session = _output_session()
    mind.output_session = output_session
    permissions = preset_permissions("auto")
    root_agent = AgentContext.root("sid_test")
    turn_context = TurnContext.create(
        agent=(
            root_agent.child("worker", "worker", agent_id="agent_child")
            if child_agent
            else root_agent
        ),
        cid="cid_test",
        sid="sid_test",
        source="test",
        pref_config={},
        cwd=".",
        permissions=permissions,
        session_started=session_started,
        session_start_reason="initial" if session_started else "",
    )
    hook_context = HookExecutionContext.from_turn(turn_context)
    hook_scope = (
        hook_scope_factory(hook_context)
        if hook_scope_factory is not None
        else HookExecutionScope(
            context=hook_context,
            dispatcher=hooks or HookRuntime.empty(),
        )
    )
    turn_execution = TurnExecution(
        context=turn_context,
        message="hello",
        hook_scope=hook_scope,
        additional_context=additional_context,
        input_payload=build_turn_input_payload(
            "hello",
            attachments=attachments,
            extras=extras,
        ),
    )
    stream_options = {
        "exec_env": {},
        "skills": (
            list(request_skills)
            if request_skills is not None
            else [{"name": "test"}]
        ),
        "turn_execution": turn_execution,
        "session_factory": lambda *_args, **_kwargs: output_session,
    }
    if attachments:
        stream_options["attachments"] = list(attachments)
    if extras:
        stream_options["extras"] = dict(extras)
    if on_turn_input_event is not None:
        stream_options["on_turn_input_event"] = on_turn_input_event

    result = await stream.stream_turn(
        mind,
        SimpleNamespace(),
        {},
        [],
        **stream_options,
    )
    return result, mind


def test_run_result_maps_status_to_exit_code() -> None:
    assert RunResult(status="completed").exit_code == 0
    assert RunResult(status="failed", error="failed").exit_code == 1
    assert RunResult(status="incomplete").exit_code == 1
    assert RunResult(status="interrupted").exit_code == 1


@pytest.mark.anyio
async def test_tool_runtime_forwards_callback_result(monkeypatch) -> None:
    expected = RunResult(status="completed", assistant_text="done")
    mind = SimpleNamespace(
        external_mcp=None,
        client_tools=object(),
        is_service_mcp_linked=lambda: False,
    )

    async def build_context(_service, _external, *, client_registry):
        _ = client_registry
        return SimpleNamespace(session=object(), tools=[])

    async def user_flow(_session, _tools) -> RunResult:
        return expected

    monkeypatch.setattr(tool_runtime, "build_tool_context", build_context)

    runtime = tool_runtime.CompositeToolRuntime(mind)
    result = await runtime.with_session({}, user_flow)

    assert result is expected


@pytest.mark.anyio
async def test_stream_returns_completed_result(monkeypatch) -> None:
    result, mind = await _run_stream(monkeypatch, [
        {"type": "text.delta", "text": "answer"},
        {"type": "text.done"},
        {"type": "turn.done", "usage": {"output_tokens": 3}},
    ])

    assert result == RunResult(
        status="completed",
        assistant_text="answer",
        usage={"output_tokens": 3},
    )
    assert mind.remembered == ["answer"]
    assert mind.output_session.content.items == [
        AssistantTextDelta("answer"),
        AssistantSegmentCompleted(),
        SourcesOutput(()),
    ]
    assert [
        entry["event"] for entry in mind.transcripts.entries
    ] == [
        "turn.started",
        "message.created",
        "message.created",
        "turn.completed",
    ]
    assert mind.transcripts.entries[1]["actor"] == "user"
    assert mind.transcripts.entries[2] == {
        "event": "message.created",
        "actor": "assistant",
        "payload": {"content": "answer"},
    }
    assert not hasattr(mind, "hook_scope")


@pytest.mark.anyio
async def test_stream_drains_logical_settlement_after_interrupted_done(
    monkeypatch,
) -> None:
    input_events = []

    result, mind = await _run_stream(
        monkeypatch,
        [
            {
                "type": "turn.done",
                "turn_id": "turn_test",
                "status": "interrupted",
            },
            {
                "type": "turn.logical_settled",
                "turn_id": "turn_test",
                "next_input": {
                    "client_message_id": "message_1",
                    "text": "continue next",
                    "attachments": [],
                    "extras": {},
                },
            },
        ],
        on_turn_input_event=input_events.append,
    )

    assert result.status == "interrupted"
    assert len(input_events) == 1
    assert input_events[0].next_input.text == "continue next"
    assert mind.transcripts.entries[-1]["event"] == "turn.interrupted"


@pytest.mark.anyio
@pytest.mark.parametrize("settlement_first", (False, True))
async def test_stream_closes_after_done_and_settlement_without_waiting_for_eof(
    monkeypatch,
    settlement_first,
) -> None:
    done = parse_stream_event({
        "type": "turn.done",
        "turn_id": "turn_test",
    })
    settled = parse_stream_event({
        "type": "turn.logical_settled",
        "turn_id": "turn_test",
        "next_input": None,
    })
    terminal_events = (
        (settled, done)
        if settlement_first
        else (done, settled)
    )

    async def open_stream(*_args, **_kwargs):
        for event in terminal_events:
            yield event
        await asyncio.Future()

    result, _mind = await asyncio.wait_for(
        _run_stream(
            monkeypatch,
            [],
            stream_factory=open_stream,
        ),
        timeout=1.0,
    )

    assert result.status == "completed"


@pytest.mark.anyio
async def test_turn_start_opens_the_control_event_boundary(monkeypatch) -> None:
    input_events = []

    result, _mind = await _run_stream(
        monkeypatch,
        [
            {"type": "turn.start", "turn_id": "turn_test"},
            {"type": "turn.done", "turn_id": "turn_test"},
        ],
        on_turn_input_event=input_events.append,
    )

    assert result.status == "completed"
    assert [event.type for event in input_events] == ["turn.start"]


@pytest.mark.anyio
async def test_sampling_accepted_input_preserves_local_transcript_order(
    monkeypatch,
) -> None:
    accepted = TurnInput(
        client_message_id="message_1",
        text="change direction",
        attachments=({"kind": "image"},),
        extras={"source": "tui"},
    )

    def handle_input(event):
        if isinstance(event, TurnInputAcceptedEvent):
            return accepted
        return None

    _result, mind = await _run_stream(
        monkeypatch,
        [
            {"type": "text.delta", "text": "before"},
            {
                "type": "turn.input.accepted",
                "turn_id": "turn_test",
                "client_message_id": "message_1",
            },
            {"type": "text.delta", "text": "after"},
            {"type": "text.done"},
            {"type": "turn.done", "turn_id": "turn_test"},
        ],
        on_turn_input_event=handle_input,
    )

    messages = [
        (entry["actor"], entry["payload"])
        for entry in mind.transcripts.entries
        if entry["event"] == "message.created"
    ]
    assert messages == [
        ("user", {"content": "hello"}),
        ("assistant", {"content": "before"}),
        (
            "user",
            {
                "content": "change direction",
                "attachments": [{"kind": "image"}],
                "extras": {"source": "tui"},
            },
        ),
        ("assistant", {"content": "after"}),
    ]


@pytest.mark.anyio
async def test_transcript_preserves_assistant_tool_output_order(monkeypatch) -> None:
    _result, mind = await _run_stream(monkeypatch, [
        {"type": "text.delta", "text": "before"},
        {"type": "text.done"},
        {
            "type": "tool.output",
            "name": "remote_tool",
            "call_id": "call-1",
            "arguments": {"value": 1},
            "status": "completed",
            "result": {"ok": True, "text": "done"},
        },
        {"type": "text.delta", "text": "after"},
        {"type": "text.done"},
        {"type": "turn.done", "usage": {}},
    ])

    ordered = [
        (entry["event"], entry["actor"], entry["payload"])
        for entry in mind.transcripts.entries
        if entry["actor"] in {"assistant", "tool"}
    ]

    assert ordered[0] == (
        "message.created",
        "assistant",
        {"content": "before"},
    )
    assert ordered[1][0:2] == ("tool.completed", "tool")
    assert ordered[2] == (
        "message.created",
        "assistant",
        {"content": "after"},
    )


@pytest.mark.anyio
async def test_transcript_records_user_replay_payload(monkeypatch) -> None:
    _result, mind = await _run_stream(
        monkeypatch,
        [{"type": "turn.done", "usage": {}}],
        attachments=({"filename": "screen.png"},),
        extras={"selection": "src/app.py"},
    )

    user_entry = next(
        entry
        for entry in mind.transcripts.entries
        if entry["actor"] == "user"
    )
    assert user_entry["payload"] == {
        "content": "hello",
        "attachments": [{"filename": "screen.png"}],
        "extras": {"selection": "src/app.py"},
    }


@pytest.mark.anyio
async def test_child_stream_does_not_mutate_root_frontend_state(monkeypatch) -> None:
    result, mind = await _run_stream(
        monkeypatch,
        [
            {"type": "text.delta", "text": "child answer"},
            {"type": "turn.done"},
        ],
        child_agent=True,
        frontend_active=False,
    )

    assert result.status == "completed"
    assert result.assistant_text == "child answer"
    assert mind.remembered == []
    mind.stop_anim.assert_not_awaited()


@pytest.mark.anyio
async def test_child_stream_failure_does_not_stop_root_animation(monkeypatch) -> None:
    async def fail_stream(*_args, **_kwargs):
        raise RuntimeError("child stream failed")
        if False:
            yield None

    result, mind = await _run_stream(
        monkeypatch,
        [],
        child_agent=True,
        frontend_active=False,
        stream_factory=fail_stream,
    )

    assert result.status == "failed"
    mind.stop_anim.assert_not_awaited()


@pytest.mark.anyio
async def test_stream_preserves_explicit_empty_skills(monkeypatch) -> None:
    async def stream_with_no_skills(*_args, **kwargs):
        assert kwargs["skills"] == []
        yield parse_stream_event({"type": "turn.done"})

    result, _mind_state = await _run_stream(
        monkeypatch,
        [],
        request_skills=(),
        stream_factory=stream_with_no_skills,
    )

    assert result.status == "completed"


@pytest.mark.anyio
async def test_stream_forwards_turn_additional_context(monkeypatch) -> None:
    async def stream_with_context(*_args, **kwargs):
        assert kwargs["additional_context"] == [
            "inspect security boundaries",
            "check cancellation paths",
        ]
        yield parse_stream_event({"type": "turn.done"})

    result, _mind_state = await _run_stream(
        monkeypatch,
        [],
        additional_context=(
            "inspect security boundaries",
            "check cancellation paths",
        ),
        stream_factory=stream_with_context,
    )

    assert result.status == "completed"


@pytest.mark.anyio
async def test_stream_forwards_turn_hook_context(monkeypatch) -> None:
    class CommandRunner(object):
        async def execute(self, definition, _payload):
            if definition.event == "SessionStart":
                return SimpleNamespace(data={
                    "systemMessage": "session system",
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": "session context",
                    },
                })
            return SimpleNamespace(data={
                "systemMessage": "prompt system",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "prompt context",
                },
            })

    definitions = resolve_hook_definitions(
        {
            "SessionStart": [_hook("start", matcher="startup")],
            "UserPromptSubmit": [_hook("prompt")],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    async def stream_with_context(*_args, **kwargs):
        assert kwargs["additional_context"] == [
            "session context",
            "prompt context",
        ]
        assert "system_message" not in kwargs
        yield parse_stream_event({"type": "turn.done"})

    result, _mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        session_started=True,
        stream_factory=stream_with_context,
    )

    assert result.status == "completed"


@pytest.mark.anyio
async def test_session_start_stop_queues_context_for_next_turn(monkeypatch) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
                "continue": False,
                "stopReason": "configure first",
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "Python 3.13 is required",
                },
            })

    definitions = resolve_hook_definitions(
        {"SessionStart": [_hook("start", matcher="startup")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        session_started=True,
    )

    assert result.status == "failed"
    assert result.error == "configure first"
    assert mind_state.queued_context == [("Python 3.13 is required",)]


@pytest.mark.anyio
async def test_prompt_stop_queues_context_for_next_turn(monkeypatch) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
                "continue": False,
                "stopReason": "Select a project first.",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        "Available projects: web, app, service."
                    ),
                },
            })

    definitions = resolve_hook_definitions(
        {"UserPromptSubmit": [_hook("prompt")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "failed"
    assert result.error == "Select a project first."
    assert mind_state.queued_context == [(
        "Available projects: web, app, service.",
    )]


@pytest.mark.anyio
async def test_child_prompt_stop_returns_context_without_queuing_root(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
                "continue": False,
                "stopReason": "Select a project first.",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": (
                        "Available projects: web, app, service."
                    ),
                },
            })

    definitions = resolve_hook_definitions(
        {"UserPromptSubmit": [_hook("prompt")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
        child_agent=True,
    )

    assert result.status == "failed"
    assert result.additional_context == (
        "Available projects: web, app, service.",
    )
    assert mind_state.queued_context == []


@pytest.mark.anyio
async def test_stream_runs_turn_hooks_from_one_scope(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            return SimpleNamespace(data={})

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "SessionStart": [_hook("start", matcher="startup")],
            "UserPromptSubmit": [_hook("prompt")],
            "Stop": [_hook("stop")],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done", "usage": {"output_tokens": 2}}],
        hooks=HookRuntime(definitions, command_runner=runner),
        session_started=True,
    )

    assert result.status == "completed"
    assert [event for event, _payload in runner.calls] == [
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
    ]
    assert runner.calls[0][1]["source"] == "startup"
    assert runner.calls[1][1]["prompt"] == "hello"
    assert runner.calls[2][1]["stop_hook_active"] is False
    assert runner.calls[2][1]["last_assistant_message"] is None
    assert mind_state.transcripts.entries[0] == {
        "event": "session.started",
        "actor": "system",
        "payload": {
            "cwd": ".",
            "source": "test",
            "reason": "initial",
            "model": "",
        },
    }


@pytest.mark.anyio
async def test_child_stream_skips_root_session_and_stop_lifecycle_hooks(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.events = []

        async def execute(self, definition, _payload):
            self.events.append(definition.event)
            if definition.event == "SessionStart":
                return SimpleNamespace(data={
                    "continue": False,
                    "stopReason": "root startup policy",
                })
            if definition.event == "Stop":
                return SimpleNamespace(data={
                    "decision": "block",
                    "reason": "root continuation",
                })
            return SimpleNamespace(data={})

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "SessionStart": [_hook("start", matcher="startup")],
            "UserPromptSubmit": [_hook("prompt")],
            "Stop": [_hook("stop")],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, _mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done"}],
        hooks=HookRuntime(definitions, command_runner=runner),
        session_started=True,
        child_agent=True,
    )

    assert result.status == "completed"
    assert runner.events == ["UserPromptSubmit"]


@pytest.mark.anyio
async def test_stream_reuses_injected_hook_scope_snapshot(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.events = []

        async def execute(self, definition, _payload):
            self.events.append(definition.event)
            return SimpleNamespace(data={})

    injected_runner = CommandRunner()
    resolved_runner = CommandRunner()
    injected_definitions = resolve_hook_definitions(
        {
            "UserPromptSubmit": [_hook("injected-prompt")],
            "Stop": [_hook("injected-stop")],
        },
        source_scope="user",
        source_path=Path("injected.toml"),
    )
    resolved_definitions = resolve_hook_definitions(
        {
            "UserPromptSubmit": [_hook("resolved-prompt")],
            "Stop": [_hook("resolved-stop")],
        },
        source_scope="user",
        source_path=Path("resolved.toml"),
    )
    injected_runtime = HookRuntime(
        injected_definitions,
        command_runner=injected_runner,
    )
    resolved_runtime = HookRuntime(
        resolved_definitions,
        command_runner=resolved_runner,
    )

    result, mind = await _run_stream(
        monkeypatch,
        [{"type": "turn.done"}],
        hooks=resolved_runtime,
        hook_scope_factory=lambda context: HookExecutionScope(
            context=context,
            dispatcher=injected_runtime,
        ),
    )

    assert result.status == "completed"
    assert injected_runner.events == ["UserPromptSubmit", "Stop"]
    assert resolved_runner.events == []
    assert not hasattr(mind, "hook_scope")


@pytest.mark.anyio
async def test_prompt_hook_denial_skips_stop_and_continuation(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            if definition.event == "UserPromptSubmit":
                return SimpleNamespace(data={
                    "continue": False,
                    "stopReason": "prompt blocked",
                })
            return SimpleNamespace(data={
                "decision": "block",
                "reason": "retry blocked prompt",
            })

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "UserPromptSubmit": [_hook("prompt")],
            "Stop": [_hook("stop")],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, _mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done"}],
        hooks=HookRuntime(definitions, command_runner=runner),
    )

    assert result.status == "failed"
    assert result.error == "prompt blocked"
    assert [event for event, _payload in runner.calls] == ["UserPromptSubmit"]


@pytest.mark.anyio
async def test_stop_hook_failure_does_not_replace_completed_result(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, definition, _payload):
            if definition.event == "Stop":
                raise RuntimeError("stop hook failed")
            return SimpleNamespace(data={})

    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )

    result, _mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done", "usage": {"output_tokens": 2}}],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result == RunResult(
        status="completed",
        usage={"output_tokens": 2},
    )


@pytest.mark.anyio
async def test_stream_cancellation_reports_interrupted_stop_hook(
    monkeypatch,
) -> None:
    started = asyncio.Event()

    async def pending_stream(*_args, **_kwargs):
        started.set()
        await asyncio.Future()
        if False:
            yield None

    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            return SimpleNamespace(data={
                "decision": "block",
                "reason": "should be ignored",
            })

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    task = asyncio.create_task(_run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=runner),
        stream_factory=pending_stream,
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [event for event, _payload in runner.calls] == ["Stop"]
    assert runner.calls[0][1]["stop_hook_active"] is False


@pytest.mark.anyio
async def test_stop_hook_continuation_runs_another_turn(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.payloads = []

        async def execute(self, _definition, payload):
            self.payloads.append(payload)
            if not payload["stop_hook_active"]:
                return SimpleNamespace(data={
                    "decision": "block",
                    "reason": "continue once",
                    "systemMessage": "stop system",
                })
            return SimpleNamespace(data={})

    definitions = resolve_hook_definitions(
        {"Stop": [_hook("stop")]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    runner = CommandRunner()
    messages = []
    request_kwargs = []

    async def stream_with_continuation(_pref, message, _tools, **kwargs):
        messages.append(message)
        request_kwargs.append(kwargs)
        yield parse_stream_event({
            "type": "text.delta",
            "text": f"reply {len(messages)}",
        })
        yield parse_stream_event({"type": "turn.done"})

    result, _mind_state = await _run_stream(
        monkeypatch,
        [],
        hooks=HookRuntime(definitions, command_runner=runner),
        stream_factory=stream_with_continuation,
    )

    assert result.status == "completed"
    assert result.assistant_text == "reply 2"
    assert messages == ["hello", "continue once"]
    assert "additional_context" not in request_kwargs[1]
    assert "system_message" not in request_kwargs[1]
    assert [payload["stop_hook_active"] for payload in runner.payloads] == [
        False,
        True,
    ]


@pytest.mark.anyio
async def test_stream_returns_failed_result(monkeypatch) -> None:
    result, _mind_state = await _run_stream(monkeypatch, [
        {"type": "turn.failed", "error": "request failed"},
    ])

    assert result.status == "failed"
    assert result.error == "request failed"
    assert result.exit_code == 1


@pytest.mark.anyio
async def test_stream_without_terminal_event_is_incomplete(monkeypatch) -> None:
    result, _mind_state = await _run_stream(monkeypatch, [])

    assert result.status == "incomplete"
    assert result.error == "stream ended before turn completion"


@pytest.mark.anyio
async def test_stream_emits_assistant_boundary_before_structured_output(monkeypatch) -> None:
    result, mind = await _run_stream(monkeypatch, [
        {"type": "text.delta", "text": "first"},
        {"type": "text.done"},
        {"type": "tool.builtin.call"},
        {"type": "tool.builtin.done"},
        {"type": "text.delta", "text": "second"},
        {"type": "text.done"},
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    assert mind.output_session.content.items == [
        AssistantTextDelta("first"),
        AssistantSegmentCompleted(),
        AssistantOutputBoundary(),
        AssistantTextDelta("second"),
        AssistantSegmentCompleted(),
        SourcesOutput(()),
    ]


@pytest.mark.anyio
async def test_stream_reports_client_tool_result_from_turn_context(monkeypatch) -> None:
    invocations = []
    posted = []

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        invocations.append(invocation)
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="done",
                call_id=invocation.call_id,
                fields={"ok": True, "text": "done"},
            )
        )

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _mind_state = await _run_stream(monkeypatch, [
        {
            "type": "tool.call",
            "cid": "untrusted-cid",
            "sid": "untrusted-sid",
            "call_id": "call-client",
            "name": "test_tool",
            "arguments": {"value": 1},
            "execution": {"target": "client"},
        },
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    assert invocations[0].turn.cid == "cid_test"
    assert invocations[0].turn.sid == "sid_test"
    posted_args, posted_kwargs = posted[0]
    assert posted_args == (
        "cid_test",
        "sid_test",
        "call-client",
        "test_tool",
        True,
        {"ok": True, "text": "done"},
    )
    assert posted_kwargs == {"execution": {"target": "client"}}


@pytest.mark.anyio
async def test_stream_reports_plan_result_after_local_execution(monkeypatch) -> None:
    posted = []

    async def handle(_runner, *, invocation):
        assert invocation.name == PLAN_STEPS_TOOL
        return PlanExecutionReport(
            ok=True,
            text="planned",
            data={"steps": 1},
            attachments=[],
            cost_ms=5,
            results=[],
            additional_context=("nested tool context",),
            system_message="Nested tool system message.",
        )

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.PlanToolCallRunner, "handle", handle)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _mind_state = await _run_stream(monkeypatch, [
        {
            "type": "tool.call",
            "cid": "untrusted-cid",
            "sid": "untrusted-sid",
            "call_id": "call-plan",
            "name": PLAN_STEPS_TOOL,
            "arguments": {"steps": []},
        },
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    assert posted[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call-plan",
        PLAN_STEPS_TOOL,
        True,
    )
    assert posted[0][0][5]["data"] == {"steps": 1}
    assert posted[0][1]["additional_context"] == ("nested tool context",)
    assert posted[0][1]["system_message"] == "Nested tool system message."


@pytest.mark.anyio
async def test_stream_queues_pre_tool_context_after_operation_error(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": "inspect protected paths",
                },
            })

    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [
                _hook("pre", matcher=PLAN_STEPS_TOOL),
            ],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )

    async def fail(_runner, *, invocation):
        _ = invocation
        raise RuntimeError("plan operation failed")

    monkeypatch.setattr(stream.PlanToolCallRunner, "handle", fail)

    result, mind_state = await _run_stream(
        monkeypatch,
        [{
            "type": "tool.call",
            "call_id": "call-plan",
            "name": PLAN_STEPS_TOOL,
            "arguments": {"steps": []},
        }],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "failed"
    assert result.additional_context == ("inspect protected paths",)
    assert mind_state.queued_context == [("inspect protected paths",)]


@pytest.mark.anyio
async def test_post_tool_hook_replaces_plan_result_for_model(monkeypatch) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
                "replacementResult": {
                    "ok": False,
                    "text": "plan result replaced",
                    "data": {"replaced": True},
                },
                "systemMessage": "Use the replacement result.",
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "explain replacement",
                },
            })

    definitions = resolve_hook_definitions(
        {"PostToolUse": [_hook("replace", matcher=PLAN_STEPS_TOOL)]},
        source_scope="user",
        source_path=Path("config.toml"),
    )
    posted = []

    async def handle(_runner, *, invocation):
        assert invocation.name == PLAN_STEPS_TOOL
        return PlanExecutionReport(
            ok=True,
            text="planned",
            data={"steps": 1},
            attachments=[],
            cost_ms=5,
            results=[],
        )

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.PlanToolCallRunner, "handle", handle)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _mind_state = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.call",
                "call_id": "call-plan",
                "name": PLAN_STEPS_TOOL,
                "arguments": {"steps": []},
            },
            {"type": "turn.done"},
        ],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "completed"
    assert posted[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call-plan",
        PLAN_STEPS_TOOL,
        False,
    )
    assert posted[0][0][5]["data"] == {"replaced": True}
    assert posted[0][1]["additional_context"] == ("explain replacement",)
    assert "system_message" not in posted[0][1]


@pytest.mark.anyio
async def test_child_approval_uses_local_agent_identity(monkeypatch) -> None:
    approval_posts = []

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, mind = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.approval_required",
                "call_id": "call-child",
                "name": "shell_command",
                "approval": {
                    "id": "approval-child",
                    "tool": "shell_command",
                    "command": "pytest -q",
                    "agent_id": "spoofed",
                    "agent_type": "spoofed",
                },
            },
            {"type": "turn.done"},
        ],
        child_agent=True,
    )

    assert result.status == "completed"
    approval = mind.frontend.interaction.request_approval.await_args.args[0]
    assert approval["agent_id"] == "agent_child"
    assert approval["agent_type"] == "worker"
    assert approval["agent_depth"] == 1
    assert approval_posts[0][0][:4] == (
        "cid_test",
        "sid_test",
        "call-child",
        "approval-child",
    )


@pytest.mark.anyio
async def test_pre_tool_approval_denial_reports_additional_context(
    monkeypatch,
) -> None:
    class CommandRunner(object):
        async def execute(self, _definition, _payload):
            return SimpleNamespace(data={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "unsafe operation",
                    "additionalContext": "Use the safe tool instead.",
                },
            })

    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "check"}],
                "matcher": "test_tool",
            }],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )
    approval_posts = []

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, mind = await _run_stream(
        monkeypatch,
        [{
            "type": "tool.approval_required",
            "call_id": "call-denied",
            "name": "test_tool",
            "arguments": {"value": 1},
            "approval": {
                "id": "approval-denied",
                "tool": "test_tool",
                "arguments": {"value": 1},
            },
        }, {"type": "turn.done"}],
        hooks=HookRuntime(definitions, command_runner=CommandRunner()),
    )

    assert result.status == "completed"
    mind.frontend.interaction.request_approval.assert_not_awaited()
    approval_kwargs = dict(approval_posts[0][1])
    assert approval_kwargs.pop("turn_id")
    assert approval_kwargs == {
        "decision": "decline",
        "reason": "unsafe operation",
        "additional_context": ("Use the safe tool instead.",),
    }


@pytest.mark.anyio
async def test_stream_uses_typed_approval_before_client_tool_call(monkeypatch) -> None:
    approval_posts = []
    result_posts = []

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="done",
                call_id=invocation.call_id,
                fields={"ok": True, "text": "done"},
            )
        )

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    async def post_tool_result(*args, **kwargs):
        result_posts.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, mind = await _run_stream(monkeypatch, [
        {
            "type": "tool.approval_required",
            "call_id": "call-approved",
            "name": "test_tool",
            "arguments": {"value": 1},
            "approval": {
                "id": "approval-1",
                "tool": "test_tool",
                "arguments": {"value": 1},
            },
        },
        {
            "type": "tool.call",
            "call_id": "call-approved",
            "name": "test_tool",
            "arguments": {"value": 1},
            "approval_id": "approval-1",
            "approved": True,
        },
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    mind.frontend.interaction.request_approval.assert_awaited_once()
    assert approval_posts[0][0] == (
        "cid_test",
        "sid_test",
        "call-approved",
        "approval-1",
    )
    approval_kwargs = dict(approval_posts[0][1])
    assert approval_kwargs.pop("turn_id")
    assert approval_kwargs == {
        "decision": "accept",
        "reason": None,
    }
    assert result_posts[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call-approved",
        "test_tool",
        True,
    )


@pytest.mark.anyio
async def test_stream_posts_only_amendment_id_for_policy_approval(
    monkeypatch,
) -> None:
    approval_posts = []

    async def request(_coordinator, approval):
        assert approval["proposed_execpolicy_amendment"]["display"] == "git clone"
        return "acceptWithExecpolicyAmendment"

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(ApprovalCoordinator, "request", request)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, _mind_state = await _run_stream(monkeypatch, [
        {
            "type": "tool.approval_required",
            "call_id": "call-amendment",
            "name": "shell_command",
            "arguments": {"command": "git clone https://example.test/repo.git"},
            "approval": {
                "id": "approval-amendment",
                "tool": "shell_command",
                "justification": "需要检查源码",
                "proposed_execpolicy_amendment": {
                    "id": "amendment_1",
                    "command_prefix": ["git", "clone"],
                    "display": "git clone",
                },
            },
        },
        {"type": "turn.done"},
    ])

    assert result.status == "completed"
    assert approval_posts[0][0] == (
        "cid_test",
        "sid_test",
        "call-amendment",
        "approval-amendment",
    )
    approval_kwargs = dict(approval_posts[0][1])
    assert approval_kwargs.pop("turn_id")
    assert approval_kwargs == {
        "decision": "acceptWithExecpolicyAmendment",
        "reason": None,
        "execpolicy_amendment_id": "amendment_1",
    }


@pytest.mark.anyio
async def test_declined_tool_closes_without_interrupting_turn(monkeypatch) -> None:
    approval_posts = []

    async def request(_coordinator, _approval):
        return "decline"

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(ApprovalCoordinator, "request", request)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, mind = await _run_stream(monkeypatch, [
        {
            "type": "tool.approval_required",
            "call_id": "call-declined",
            "name": "shell_command",
            "approval": {
                "id": "approval-declined",
                "tool": "shell_command",
            },
        },
        {
            "type": "tool.output",
            "call_id": "call-declined",
            "name": "shell_command",
            "status": "declined",
            "ok": False,
            "result": {"ok": False, "text": "user denied"},
        },
        {"type": "text.delta", "text": "我会换一种方式。"},
        {"type": "text.done"},
        {"type": "turn.done", "status": "completed"},
    ])

    assert result.status == "completed"
    assert result.assistant_text == "我会换一种方式。"
    assert approval_posts[0][1]["decision"] == "decline"
    tool_entry = next(
        entry
        for entry in mind.transcripts.entries
        if entry["event"] == "tool.completed"
    )
    assert tool_entry["payload"]["status"] == "declined"
    approval_views = [
        item
        for item in mind.output_session.presentation.items
        if isinstance(item, ApprovalView)
    ]
    assert [view.decision for view in approval_views] == ["decline"]
    assert len(mind.output_session.presentation.items) == 3


@pytest.mark.anyio
async def test_cancelled_approval_drains_interrupted_turn_settlement(
    monkeypatch,
) -> None:
    approval_posts = []
    input_events = []

    async def request(_coordinator, _approval):
        return "cancel"

    async def post_tool_approval(*args, **kwargs):
        approval_posts.append((args, kwargs))

    monkeypatch.setattr(ApprovalCoordinator, "request", request)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    result, mind = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.approval_required",
                "turn_id": "turn_cancelled",
                "call_id": "call-cancelled",
                "name": "shell_command",
                "approval": {
                    "id": "approval-cancelled",
                    "tool": "shell_command",
                },
            },
            {
                "type": "tool.output",
                "turn_id": "turn_cancelled",
                "call_id": "call-cancelled",
                "name": "shell_command",
                "status": "cancelled",
                "ok": False,
                "result": {"ok": False, "text": "user cancelled"},
            },
            {
                "type": "turn.done",
                "turn_id": "turn_cancelled",
                "status": "interrupted",
            },
            {
                "type": "turn.logical_settled",
                "turn_id": "turn_cancelled",
            },
        ],
        on_turn_input_event=input_events.append,
    )

    assert result.status == "interrupted"
    approval_kwargs = dict(approval_posts[0][1])
    assert approval_kwargs.pop("turn_id")
    assert approval_kwargs == {
        "decision": "cancel",
        "reason": "user cancelled",
    }
    tool_entry = next(
        entry
        for entry in mind.transcripts.entries
        if entry["event"] == "tool.completed"
    )
    assert tool_entry["payload"]["status"] == "cancelled"
    approval_views = [
        item
        for item in mind.output_session.presentation.items
        if isinstance(item, ApprovalView)
    ]
    assert [view.decision for view in approval_views] == ["cancel"]
    assert len(mind.output_session.presentation.items) == 2
    assert len(input_events) == 1
    assert input_events[0].type == "turn.logical_settled"


@pytest.mark.anyio
async def test_pre_tool_hook_denial_is_reported_without_execution(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, _definition, payload):
            self.calls.append(payload)
            return SimpleNamespace(data={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "blocked by test hook",
                    "additionalContext": "Use the safe tool instead.",
                },
            })

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "check"}],
                "matcher": "test_tool",
            }],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )
    posted = []

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)

    result, _mind_state = await _run_stream(
        monkeypatch,
        [
            {
                "type": "tool.call",
                "cid": "untrusted-cid",
                "sid": "untrusted-sid",
                "call_id": "call_test",
                "name": "test_tool",
                "arguments": {"value": 1},
            },
            {"type": "turn.done"},
        ],
        hooks=HookRuntime(definitions, command_runner=runner),
    )

    assert result.status == "completed"
    assert len(runner.calls) == 1
    assert posted[0][0][:5] == (
        "cid_test",
        "sid_test",
        "call_test",
        "test_tool",
        False,
    )
    assert posted[0][0][5]["data"]["hook_denied"] is True
    assert posted[0][1]["additional_context"] == (
        "Use the safe tool instead.",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "tool_name",
        "original_arguments",
        "updated_input",
        "expected_arguments",
        "expected_hook_input",
    ),
    [
        (
            "test_tool",
            {"value": 1},
            {"value": 2},
            {"value": 2},
            {"value": 1},
        ),
        (
            "shell_command",
            {
                "command": "echo original",
                "cwd": "/tmp/project",
                "timeout_sec": 120,
                "yield_time_ms": 250,
            },
            {"command": "echo rewritten"},
            {
                "command": "echo rewritten",
                "cwd": "/tmp/project",
                "timeout_sec": 120,
                "yield_time_ms": 250,
            },
            {"command": "echo original"},
        ),
        (
            "apply_patch",
            {
                "patch": "*** Begin Patch\n*** End Patch",
                "force": True,
                "expected_sha256": "sha256:original",
            },
            {"command": "*** Begin Patch\n*** Add File: safe\n+ok\n*** End Patch"},
            {
                "patch": "*** Begin Patch\n*** Add File: safe\n+ok\n*** End Patch",
                "force": True,
                "expected_sha256": "sha256:original",
            },
            {"command": "*** Begin Patch\n*** End Patch"},
        ),
    ],
)
async def test_pre_tool_updated_input_flows_through_approval_and_execution(
    monkeypatch,
    tool_name,
    original_arguments,
    updated_input,
    expected_arguments,
    expected_hook_input,
) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, _definition, payload):
            self.calls.append(payload)
            return SimpleNamespace(data={
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated_input,
                },
            })

    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "hooks": [{"type": "command", "command": "rewrite"}],
                "matcher": tool_name,
            }],
        },
        source_scope="user",
        source_path=Path("config.toml"),
    )
    runner = CommandRunner()
    executed = []
    posted = []

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        executed.append(dict(invocation.arguments))
        return ClientToolCallOutcome(
            result=ClientToolCallResult(
                name=invocation.name,
                arguments=dict(invocation.arguments),
                ok=True,
                text="done",
                call_id=invocation.call_id,
                fields={"ok": True, "text": "done"},
            )
        )

    async def post_tool_result(*args, **kwargs):
        posted.append((args, kwargs))
        return {}

    async def post_tool_approval(*_args, **_kwargs):
        return None

    monkeypatch.setattr(stream.ClientToolCallRunner, "execute", execute)
    monkeypatch.setattr(stream, "post_tool_result", post_tool_result)
    monkeypatch.setattr(stream, "post_tool_approval", post_tool_approval)

    approval_event = {
        "type": "tool.approval_required",
        "call_id": "call-rewrite",
        "name": tool_name,
        "arguments": original_arguments,
        "approval": {
            "id": "approval-rewrite",
            "tool": tool_name,
            "arguments": original_arguments,
        },
    }
    call_event = {
        "type": "tool.call",
        "call_id": "call-rewrite",
        "name": tool_name,
        "arguments": original_arguments,
        "approval_id": "approval-rewrite",
        "approved": True,
    }
    if tool_name == "shell_command":
        execution = {
            "target": "local",
            "state": "approved",
            "grantId": "grant-rewrite",
            "canonicalArguments": original_arguments,
        }
        approval_event["execution"] = execution
        call_event["execution"] = execution

    result, mind = await _run_stream(
        monkeypatch,
        [
            approval_event,
            call_event,
            {"type": "turn.done"},
        ],
        hooks=HookRuntime(definitions, command_runner=runner),
    )

    assert result.status == "completed"
    approval = mind.frontend.interaction.request_approval.await_args.args[0]
    assert approval["arguments"] == expected_arguments
    if tool_name in {"shell_command", "apply_patch"}:
        command_field = "patch" if tool_name == "apply_patch" else "command"
        assert approval["command"] == expected_arguments[command_field]
    assert executed == [expected_arguments]
    assert runner.calls[0]["tool_input"] == expected_hook_input
    assert posted[0][0][5] == {"ok": True, "text": "done"}
