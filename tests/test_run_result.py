# -*- coding: utf-8 -*-

import typing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mind_app.modes import stream
from mind_app.modes.result import RunResult
from mind_app.output.session import OutputSession
from mind_app.runtime.mcp import tool_runtime


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
) -> tuple[RunResult, SimpleNamespace]:
    async def stream_chat(*_args, **_kwargs):
        for event in events:
            yield event

    monkeypatch.setattr(stream, "stream_chat", stream_chat)
    mind = _mind()
    result = await stream.stream_looper(
        mind,
        SimpleNamespace(),
        "xtra",
        {},
        "hello",
        [],
        exec_env={},
        skills=[{"name": "test"}],
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
