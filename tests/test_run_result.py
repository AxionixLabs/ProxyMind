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
from mind_app.modes import stream
from mind_app.modes.result import RunResult
from mind_app.output.content import (
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    SourcesOutput,
)
from mind_app.output.session import OutputSession
from mind_app.runtime.mcp import tool_runtime
from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from mind_app.runtime.tools.client_call import ClientToolCallResult
from mind_app.runtime.tools.plan_steps import PlanExecutionReport
from mind_core.hooks import resolve_hook_definitions
from mind_core.permissions import preset_permissions
from mind_nova.stream_events import parse_stream_event


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


def _output_session() -> OutputSession:
    return OutputSession(
        control=_OutputControl(),
        status=_OutputStatus(),
        content=_Sink(),
        presentation=_Sink(),
    )


def _mind() -> SimpleNamespace:
    remembered: list[str] = []

    async def await_cleanup(awaitable) -> None:
        await awaitable

    return SimpleNamespace(
        report=SimpleNamespace(log_papers=[]),
        frontend=SimpleNamespace(
            runtime=SimpleNamespace(active=True),
            interaction=SimpleNamespace(
                request_approval=AsyncMock(return_value="accept"),
            ),
        ),
        stop_anim=AsyncMock(),
        await_cleanup=await_cleanup,
        remember_last_assistant_reply=remembered.append,
        remembered=remembered,
    )


async def _run_stream(
    monkeypatch,
    events: list[dict[str, typing.Any]],
    *,
    hooks: HookRuntime | None = None,
    session_started: bool = False,
    stream_factory: typing.Callable[
        ..., typing.AsyncIterator[typing.Any]
    ] | None = None,
) -> tuple[RunResult, SimpleNamespace]:
    if stream_factory is None:
        async def stream_chat(*_args, **_kwargs):
            for payload in events:
                yield parse_stream_event(payload)
    else:
        stream_chat = stream_factory

    monkeypatch.setattr(stream, "stream_chat", stream_chat)
    mind = _mind()
    output_session = _output_session()
    mind.output_session = output_session
    permissions = preset_permissions("auto")
    turn_context = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        mode="xtra",
        source="test",
        pref_config={},
        cwd=".",
        permissions=permissions,
        session_started=session_started,
        session_start_reason="initial" if session_started else "",
    )
    hook_context = HookExecutionContext.from_turn(turn_context)
    mind.hook_scope = Mock(return_value=HookExecutionScope(
        context=hook_context,
        dispatcher=hooks or HookRuntime.empty(),
    ))
    result = await stream.stream_looper(
        mind,
        SimpleNamespace(),
        "xtra",
        {},
        "hello",
        [],
        exec_env={},
        skills=[{"name": "test"}],
        permissions=permissions,
        turn_context=turn_context,
        session_factory=lambda *_args, **_kwargs: output_session,
    )
    return result, mind


def test_run_result_maps_status_to_exit_code() -> None:
    assert RunResult(status="completed").exit_code == 0
    assert RunResult(status="failed", error="failed").exit_code == 1
    assert RunResult(status="incomplete").exit_code == 1


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
    mind.hook_scope.assert_called_once()
    assert mind.hook_scope.call_args.args[0].session_id == "sid_test"


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
            "SessionStart": [{
                "command": "start",
                "matcher": "initial",
            }],
            "UserPromptSubmit": [{"command": "prompt"}],
            "Stop": [{"command": "stop"}],
        },
        source_scope="user",
        source_path=Path("hooks.toml"),
    )

    result, _mind_state = await _run_stream(
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
    assert runner.calls[0][1]["session_started"] is True
    assert runner.calls[1][1]["prompt"] == "hello"
    assert runner.calls[2][1]["outcome"] == "completed"
    assert runner.calls[2][1]["usage"] == {"output_tokens": 2}


@pytest.mark.anyio
async def test_stream_stops_after_prompt_hook_denial(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, definition, payload):
            self.calls.append((definition.event, payload))
            if definition.event == "UserPromptSubmit":
                return SimpleNamespace(data={
                    "continue": False,
                    "reason": "prompt blocked",
                })
            return SimpleNamespace(data={})

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "UserPromptSubmit": [{"command": "prompt"}],
            "Stop": [{"command": "stop"}],
        },
        source_scope="user",
        source_path=Path("hooks.toml"),
    )

    result, _mind_state = await _run_stream(
        monkeypatch,
        [{"type": "turn.done"}],
        hooks=HookRuntime(definitions, command_runner=runner),
    )

    assert result.status == "failed"
    assert result.error == "prompt blocked"
    assert [event for event, _payload in runner.calls] == [
        "UserPromptSubmit",
        "Stop",
    ]
    assert runner.calls[1][1]["outcome"] == "failed"


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
        {"Stop": [{"command": "stop"}]},
        source_scope="user",
        source_path=Path("hooks.toml"),
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
            return SimpleNamespace(data={})

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {"Stop": [{"command": "stop"}]},
        source_scope="user",
        source_path=Path("hooks.toml"),
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
    assert runner.calls[0][1]["outcome"] == "interrupted"


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
        return ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=True,
            text="done",
            call_id=invocation.call_id,
            fields={"ok": True, "text": "done"},
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


@pytest.mark.anyio
async def test_stream_uses_typed_approval_before_client_tool_call(monkeypatch) -> None:
    approval_posts = []
    result_posts = []

    async def execute(_runner, invocation, *, use_coding_trace, display=True):
        _ = use_coding_trace, display
        return ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=True,
            text="done",
            call_id=invocation.call_id,
            fields={"ok": True, "text": "done"},
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
    assert approval_posts[0][1] == {
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
async def test_pre_tool_hook_denial_is_reported_without_execution(monkeypatch) -> None:
    class CommandRunner(object):
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, _definition, payload):
            self.calls.append(payload)
            return SimpleNamespace(data={
                "decision": "deny",
                "reason": "blocked by test hook",
            })

    runner = CommandRunner()
    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [{
                "command": "check",
                "matcher": "test_tool",
            }],
        },
        source_scope="user",
        source_path=Path("hooks.toml"),
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
