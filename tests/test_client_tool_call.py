# -*- coding: utf-8 -*-

from types import SimpleNamespace
from typing import cast
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from mind_app.runtime.execution import (
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from mind_app.runtime.hooks.models import (
    HookVisibleToolResult,
    ToolCallRunResult
)
from mind_app.runtime.tools import client_call
from mind_app.runtime.tools.client_call import (
    ClientToolCallOutcome,
    ClientToolCallResult,
    ClientToolCallRunner,
    build_client_tool_post_kwargs,
)
from mind_core.permissions import preset_permissions


def _invocation() -> ToolInvocation:
    turn = TurnContext.create(
        agent=AgentContext.root("sid-test"),
        cid="cid-test",
        sid="sid-test",
        mode="xtra",
        source="test",
        pref_config={},
        cwd=".",
        permissions=preset_permissions("auto"),
    )
    return ToolInvocation(
        turn=turn,
        call_id="call-1",
        name="test_tool",
        arguments={"value": 1},
    )


def _runner(coordinator) -> tuple[ClientToolCallRunner, SimpleNamespace]:
    output = SimpleNamespace(record_tool_arguments=Mock())
    status = SimpleNamespace(end_status=AsyncMock())
    presentation = SimpleNamespace(emit=AsyncMock())
    runner = ClientToolCallRunner(
        session=SimpleNamespace(),
        output_control=output,
        status_control=status,
        presentation=presentation,
        tools=[{"name": "test_tool"}],
        pref_config={},
        tool_call_coordinator=coordinator,
    )
    return runner, SimpleNamespace(
        output=output,
        status=status,
        presentation=presentation,
    )


@pytest.mark.anyio
async def test_client_tool_call_executes_invocation_arguments(monkeypatch) -> None:
    async def run_allowed(invocation, operation):
        operation_result = await operation(invocation)
        return ToolCallRunResult(
            allowed=True,
            value=operation_result.value,
            visible_result=HookVisibleToolResult(
                ok=operation_result.snapshot.ok,
                text=operation_result.snapshot.text,
                fields=operation_result.snapshot.fields,
            ),
        )

    coordinator = SimpleNamespace(run_invocation=AsyncMock(side_effect=run_allowed))
    runner, ports = _runner(coordinator)
    tool_run = SimpleNamespace(
        ok=True,
        fields={"ok": True, "text": "done"},
        text="done",
        cost_ms=7,
    )
    run_tool_step = AsyncMock(return_value=tool_run)
    show_start = AsyncMock()
    show_result = AsyncMock()
    monkeypatch.setattr(client_call, "run_tool_step", run_tool_step)
    monkeypatch.setattr(client_call, "show_tool_start", show_start)
    monkeypatch.setattr(client_call, "show_tool_result", show_result)

    outcome = await runner.execute(
        _invocation(),
        use_coding_trace=False,
    )
    result = outcome.result

    assert result.ok is True
    assert result.arguments == {"value": 1}
    assert result.fields == {"ok": True, "text": "done"}
    assert result.cost_ms == 7
    ports.output.record_tool_arguments.assert_called_once_with(
        "test_tool",
        {"value": 1},
        call_id="call-1",
    )
    assert run_tool_step.await_args.kwargs["invocation"].arguments == {"value": 1}
    show_start.assert_awaited_once()
    show_result.assert_awaited_once()


@pytest.mark.anyio
async def test_client_tool_call_converts_execution_error(monkeypatch) -> None:
    async def run_allowed(invocation, operation):
        operation_result = await operation(invocation)
        return ToolCallRunResult(
            allowed=True,
            value=operation_result.value,
            visible_result=HookVisibleToolResult(
                ok=operation_result.snapshot.ok,
                text=operation_result.snapshot.text,
                fields=operation_result.snapshot.fields,
            ),
        )

    coordinator = SimpleNamespace(run_invocation=AsyncMock(side_effect=run_allowed))
    runner, _ports = _runner(coordinator)
    monkeypatch.setattr(
        client_call,
        "run_tool_step",
        AsyncMock(side_effect=RuntimeError("failed")),
    )
    monkeypatch.setattr(client_call, "show_tool_start", AsyncMock())
    monkeypatch.setattr(client_call, "show_tool_result", AsyncMock())

    outcome = await runner.execute(
        _invocation(),
        use_coding_trace=False,
    )
    result = outcome.result

    assert result.ok is False
    assert result.text == "RuntimeError: failed"
    assert result.fields["data"] == {"error": "RuntimeError: failed"}
    client_call.show_tool_result.assert_not_awaited()


@pytest.mark.anyio
async def test_client_tool_call_returns_hook_denial_without_execution(
    monkeypatch,
) -> None:
    coordinator = SimpleNamespace(run_invocation=AsyncMock(return_value=ToolCallRunResult(
        allowed=False,
        reason="blocked",
    )))
    runner, _ports = _runner(coordinator)
    run_tool_step = AsyncMock()
    monkeypatch.setattr(client_call, "run_tool_step", run_tool_step)

    outcome = await runner.execute(
        _invocation(),
        use_coding_trace=False,
    )
    result = outcome.result

    assert result.ok is False
    assert result.text == "blocked"
    assert result.fields["data"] == {
        "hook_denied": True,
        "error": "blocked",
    }
    run_tool_step.assert_not_awaited()


@pytest.mark.anyio
async def test_client_tool_call_applies_post_hook_replacement(
    monkeypatch,
) -> None:
    async def run_allowed(invocation, operation):
        operation_result = await operation(invocation)
        return ToolCallRunResult(
            allowed=True,
            value=operation_result.value,
            visible_result=HookVisibleToolResult(
                ok=False,
                text="replacement",
                fields={
                    "ok": False,
                    "text": "replacement",
                    "data": {"redacted": True},
                },
                additional_context=("review replacement",),
                system_message="Prefer the replacement.",
            ),
        )

    coordinator = SimpleNamespace(run_invocation=AsyncMock(side_effect=run_allowed))
    runner, _ports = _runner(coordinator)
    tool_run = SimpleNamespace(
        ok=True,
        fields={"ok": True, "text": "original"},
        text="original",
        cost_ms=3,
    )
    monkeypatch.setattr(client_call, "run_tool_step", AsyncMock(return_value=tool_run))
    monkeypatch.setattr(client_call, "show_tool_start", AsyncMock())
    monkeypatch.setattr(client_call, "show_tool_result", AsyncMock())

    outcome = await runner.execute(
        _invocation(),
        use_coding_trace=False,
    )
    result = outcome.result

    assert result.ok is False
    assert result.text == "replacement"
    assert result.fields["data"] == {"redacted": True}
    assert outcome.additional_context == ("review replacement",)
    assert outcome.system_message == "Prefer the replacement."


def test_client_tool_post_kwargs_includes_hook_feedback() -> None:
    outcome = ClientToolCallOutcome(
        result=ClientToolCallResult(
            name="test_tool",
            arguments={},
            ok=True,
            text="done",
            fields={"ok": True, "text": "done"},
        ),
        additional_context=(" context ",),
        system_message=" system ",
    )

    assert build_client_tool_post_kwargs(
        outcome,
        execution={"kind": "local"},
    ) == {
        "execution": {"kind": "local"},
        "additional_context": ("context",),
        "system_message": "system",
    }


def test_client_tool_outcome_rejects_invalid_result() -> None:
    with pytest.raises(
        TypeError,
        match="must return ClientToolCallResult",
    ):
        ClientToolCallOutcome(
            result=cast(ClientToolCallResult, SimpleNamespace())
        )


def test_client_tool_result_has_no_hook_feedback_fields() -> None:
    result = client_call.ClientToolCallResult(
        name="test_tool",
        arguments={},
        ok=True,
        text="done",
        fields={"ok": True, "text": "done"},
    )

    assert "additional_context" not in client_call.ClientToolCallResult.__slots__
