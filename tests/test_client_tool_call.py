# -*- coding: utf-8 -*-

from types import SimpleNamespace
from typing import cast
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest
from mcp import types as mcp_types

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
from mind_app.runtime.tools.run import hook_tool_response
from mind_core.permissions import preset_permissions


def _invocation() -> ToolInvocation:
    turn = TurnContext.create(
        agent=AgentContext.root("sid-test"),
        cid="cid-test",
        sid="sid-test",
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
async def test_js_repl_emits_start_trace_and_uses_javascript_status(monkeypatch) -> None:
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
    base = _invocation()
    invocation = ToolInvocation(
        turn=base.turn,
        call_id="call-js",
        name="js_repl",
        arguments={"code": "await work();", "timeout_ms": 30000},
    )
    tool_run = SimpleNamespace(
        ok=True,
        fields={"ok": True, "text": "done"},
        text="done",
        cost_ms=200,
    )
    run_tool_step = AsyncMock(return_value=tool_run)
    monkeypatch.setattr(client_call, "run_tool_step", run_tool_step)
    monkeypatch.setattr(client_call, "show_tool_start", AsyncMock())
    monkeypatch.setattr(client_call, "show_tool_result", AsyncMock())

    result = await runner.execute(invocation, use_coding_trace=True)

    assert result.result.ok is True
    assert run_tool_step.await_args.kwargs["status_text"] == "JavaScript"
    client_call.show_tool_start.assert_awaited_once_with(
        runner.presentation,
        "js_repl",
        {"code": "await work();", "timeout_ms": 30000},
        call_id="call-js",
    )
    client_call.show_tool_result.assert_awaited_once()
    ports.output.record_tool_arguments.assert_called_once_with(
        "js_repl",
        {"code": "await work();", "timeout_ms": 30000},
        call_id="call-js",
    )
    nested_dispatch = run_tool_step.await_args.kwargs["invocation"].meta[
        "_nested_tool_dispatch"
    ]
    assert callable(nested_dispatch)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "shows_start"),
    (
        ("shell_command", True),
        ("apply_patch", False),
        ("exec_command", False),
        ("write_stdin", False),
        ("js_repl_reset", False),
    ),
)
async def test_native_tool_start_trace_uses_two_stage_policy(
    monkeypatch,
    name: str,
    shows_start: bool,
) -> None:
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
    base = _invocation()
    invocation = ToolInvocation(
        turn=base.turn,
        call_id=f"call-{name}",
        name=name,
        arguments={"command": "echo ready"},
    )
    tool_run = SimpleNamespace(
        ok=True,
        fields={"ok": True, "text": "done"},
        text="done",
        cost_ms=7,
    )
    show_start = AsyncMock()
    monkeypatch.setattr(client_call, "run_tool_step", AsyncMock(return_value=tool_run))
    monkeypatch.setattr(client_call, "show_tool_start", show_start)
    monkeypatch.setattr(client_call, "show_tool_result", AsyncMock())

    await runner.execute(invocation, use_coding_trace=True)

    if shows_start:
        show_start.assert_awaited_once()
    else:
        show_start.assert_not_awaited()
    client_call.show_tool_result.assert_awaited_once()


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
    client_call.show_tool_result.assert_awaited_once()


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
    )

    assert build_client_tool_post_kwargs(
        outcome,
        execution={"kind": "local"},
    ) == {
        "execution": {"kind": "local"},
        "additional_context": ("context",),
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


def test_hook_tool_response_uses_text_for_builtin_shell() -> None:
    response = hook_tool_response(
        "shell_command",
        mcp_types.CallToolResult(content=[]),
        fields={"ok": True, "data": {"exit_code": 0}},
        text="command output",
        tools=[{
            "name": "shell_command",
            "meta": {"client_builtin": True, "domain": "client"},
        }],
    )

    assert response == "command output"


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        (
            "shell_command",
            {
                "output_lines": ["tests failed", "assert 1 == 2"],
                "stdout": "tests failed\n",
                "stderr": "assert 1 == 2\n",
                "output_limit": 24000,
            },
            "tests failed\nassert 1 == 2",
        ),
        (
            "exec_command",
            {
                "output": "compiler error: missing symbol",
                "stdout": "",
                "stderr": "compiler error: missing symbol",
            },
            "compiler error: missing symbol",
        ),
        (
            "write_stdin",
            {
                "output": "lint warning: unsafe call",
                "stdout": "lint warning: unsafe call",
                "stderr": "",
            },
            "lint warning: unsafe call",
        ),
        (
            "shell_command",
            {
                "output_lines": [],
                "stdout": "",
                "stderr": "",
                "output_limit": 24000,
            },
            "",
        ),
    ],
)
def test_hook_tool_response_uses_builtin_shell_output(
    name,
    data,
    expected,
) -> None:
    response = hook_tool_response(
        name,
        mcp_types.CallToolResult(content=[]),
        fields={"ok": True, "data": data},
        text=f"{name} lifecycle summary",
        tools=[{
            "name": name,
            "meta": {"client_builtin": True, "domain": "client"},
        }],
    )

    assert response == expected


def test_hook_tool_response_limits_combined_shell_output() -> None:
    response = hook_tool_response(
        "shell_command",
        mcp_types.CallToolResult(content=[]),
        fields={
            "ok": False,
            "data": {
                "output_lines": ["compiler error", "unsafe"],
                "output_limit": 14,
            },
        },
        text="shell_command failed exit_code=1",
        tools=[{
            "name": "shell_command",
            "meta": {"client_builtin": True, "domain": "client"},
        }],
    )

    assert response == "compiler error\n...[truncated 7 chars]"


def test_hook_tool_response_preserves_mcp_result_shape() -> None:
    result = mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text="notes")],
        structuredContent={"bytes": 5},
        isError=False,
    )

    response = hook_tool_response(
        "mcp__filesystem__read_file",
        result,
        fields={"ok": True, "text": "notes"},
        text="notes",
        tools=[{
            "name": "mcp__filesystem__read_file",
            "meta": {"external": True, "server": "filesystem"},
        }],
    )

    assert response["content"] == [{"type": "text", "text": "notes"}]
    assert response["structuredContent"] == {"bytes": 5}
    assert response["isError"] is False
