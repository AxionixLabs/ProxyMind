# -*- coding: utf-8 -*-

import typing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock
)

import pytest

from mind_app.modes import stream
from mind_app.modes.result import RunResult
from mind_app.output.session import OutputSession
from mind_app.runtime.mcp import tool_runtime
from mind_app.runtime.execution import AgentContext, TurnContext
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from mind_core.hooks import resolve_hook_definitions
from mind_core.permissions import preset_permissions


class _OutputControl(object):
    async def open(self) -> None:
        return None

    async def stop(self, *, blink: bool = True) -> None:
        _ = blink

    async def prepare_external_output(self) -> None:
        return None

    async def settle_stream(self) -> None:
        return None

    async def record_hidden_output(self, text: str) -> None:
        _ = text

    def mark_stream_boundary(self) -> None:
        return None

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
        frontend=SimpleNamespace(runtime=SimpleNamespace(active=True)),
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
) -> tuple[RunResult, SimpleNamespace]:
    async def stream_chat(*_args, **_kwargs):
        for event in events:
            yield event

    monkeypatch.setattr(stream, "stream_chat", stream_chat)
    mind = _mind()
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
        session_factory=lambda *_args, **_kwargs: _output_session(),
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
    mind.hook_scope.assert_called_once()
    assert mind.hook_scope.call_args.args[0].session_id == "sid_test"


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
                "cid": "cid_test",
                "sid": "sid_test",
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
    assert posted[0][0][2:5] == (
        "call_test",
        "test_tool",
        False,
    )
    assert posted[0][0][5]["data"]["hook_denied"] is True
