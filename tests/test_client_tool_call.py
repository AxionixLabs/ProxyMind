# -*- coding: utf-8 -*-

import typing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest
from mcp import types as mcp_types
from agent.ports import (
    EffectJournalDecision,
    EffectJournalPersistenceError,
    LocalEffectReconciliationRequired,
)
from agent.composition import open_effect_journal

from agent.application.turns.context import (
    AgentContext,
    ToolInvocation,
    TurnContext,
)
from agent.application.hooks.models import (
    HookVisibleToolResult,
    ToolCallRunResult,
    ToolOperationResult,
    ToolResultSnapshot,
)
from agent.harness.tools import client_calls as client_call
from agent.application.views.tool_execution import (
    show_tool_result,
    show_tool_start,
)
from agent.harness.tools.client_calls import (
    ClientToolCallOutcome,
    ClientToolCallResult,
    ClientToolCallRunner,
)
from agent.application.tools.execution import ToolExecutionResult
from infrastructure.mcp.tool_execution import (
    McpToolExecutionAdapter,
    hook_tool_response,
)
from agent.domain.policies import preset_permissions
from protocol.schema.stream_events import ExecutionEffect


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


def _compact_fields(
    *,
    ok: bool = True,
    text: str = "done",
    data: dict[str, typing.Any] | None = None,
) -> dict[str, typing.Any]:
    """构造客户端工具生命周期的规范化字段。"""
    return {
        "ok": ok,
        "text": text,
        "attachments": [],
        "data": {} if data is None else data,
    }


def _runner(
    coordinator,
    *,
    tools: list[dict] | None = None,
    effect_journal=None,
    effect_reconciler=None,
) -> tuple[ClientToolCallRunner, SimpleNamespace]:
    output = SimpleNamespace(record_tool_arguments=Mock())
    status = SimpleNamespace(
        begin_custom_tool_status=AsyncMock(),
        begin_tool_status=AsyncMock(),
        end_status=AsyncMock(),
    )
    presentation = SimpleNamespace(emit=AsyncMock())
    if effect_journal is None:
        effect_journal = SimpleNamespace(
            inspect=AsyncMock(),
            begin=AsyncMock(),
            commit=AsyncMock(),
            mark_unknown=AsyncMock(),
            reconciliation_result=AsyncMock(),
            mark_reconciled=AsyncMock(),
        )
    runner = ClientToolCallRunner(
        session=SimpleNamespace(),
        output_control=output,
        status_control=status,
        presentation=presentation,
        tools=tools if tools is not None else [{"name": "test_tool"}],
        pref_config={},
        tool_call_coordinator=coordinator,
        tool_execution=McpToolExecutionAdapter(),
        activity=SimpleNamespace(
            tool_started=AsyncMock(),
            tool_completed=AsyncMock(),
            approval_started=AsyncMock(),
            approval_completed=AsyncMock(),
        ),
        effect_journal=effect_journal,
        effect_reconciler=effect_reconciler,
    )
    return runner, SimpleNamespace(
        output=output,
        status=status,
        presentation=presentation,
    )


def _durable_invocation(workspace: Path) -> ToolInvocation:
    base = _invocation()
    turn = TurnContext.create(
        agent=base.turn.agent,
        cid=base.turn.cid,
        sid=base.turn.sid,
        source=base.turn.source,
        pref_config={},
        cwd=str(workspace.resolve()),
        permissions=base.turn.permissions,
    )
    return ToolInvocation(
        turn=turn,
        call_id=base.call_id,
        name=base.name,
        arguments=base.arguments,
        effect=ExecutionEffect(
            effect_id="effect_client_call",
            fingerprint="a" * 64,
            replay="manual",
        ),
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
        fields=_compact_fields(),
        text="done",
        cost_ms=7,
    )
    run_tool_step = AsyncMock(return_value=tool_run)
    show_start = AsyncMock()
    show_result = AsyncMock()
    runner.tool_execution.execute = run_tool_step
    monkeypatch.setattr(client_call, "show_tool_start", show_start)
    monkeypatch.setattr(client_call, "show_tool_result", show_result)

    outcome = await runner.execute(
        _invocation(),
        use_coding_trace=False,
    )
    result = outcome.result

    assert result.ok is True
    assert result.arguments == {"value": 1}
    assert result.fields == {
        "ok": True,
        "tool": "test_tool",
        "source": "client",
        "args": {"value": 1},
        "text": "done",
        "attachments": [],
        "data": {},
    }
    assert result.cost_ms == 7
    ports.output.record_tool_arguments.assert_called_once_with(
        "test_tool",
        {"value": 1},
        call_id="call-1",
    )
    assert run_tool_step.await_args.kwargs["invocation"].arguments == {"value": 1}


@pytest.mark.anyio
async def test_apply_patch_start_uses_read_only_preview_before_execution(monkeypatch) -> None:
    coordinator = SimpleNamespace()

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

    coordinator.run_invocation = AsyncMock(side_effect=run_allowed)
    coordinator.record_patch_start = Mock()
    runner, _ports = _runner(coordinator)
    preview = Mock(return_value={
        "ok": True,
        "data": {"files": [], "delta": {"exact": True, "changes": []}},
    })
    runner.patch_preview = preview
    tool_run = SimpleNamespace(
        ok=True,
        fields=_compact_fields(),
        text="done",
        cost_ms=1,
        result=None,
    )
    runner.tool_execution.execute = AsyncMock(return_value=tool_run)
    show_start = AsyncMock()
    show_result = AsyncMock()
    monkeypatch.setattr(client_call, "show_tool_start", show_start)
    monkeypatch.setattr(client_call, "show_tool_result", show_result)

    invocation = replace(
        _invocation(),
        name="apply_patch",
        arguments={"patch": "*** Begin Patch\n*** End Patch"},
    )
    await runner.execute(invocation, use_coding_trace=False)

    preview.assert_called_once_with(
        patch="*** Begin Patch\n*** End Patch",
        expected_sha256=None,
        force=False,
    )
    coordinator.record_patch_start.assert_called_once_with(
        invocation,
        preview_data={
            "files": [],
            "delta": {"exact": True, "changes": []},
        },
    )
    assert show_start.await_args.kwargs["patch_preview"] == {
        "files": [],
        "delta": {"exact": True, "changes": []},
    }
    show_start.assert_awaited_once()
    show_result.assert_awaited_once()


@pytest.mark.anyio
async def test_malformed_patch_preview_is_skipped_without_blocking_display() -> None:
    presentation = SimpleNamespace(emit=AsyncMock())

    await show_tool_start(
        presentation,
        "apply_patch",
        {"patch": "patch"},
        patch_preview={"files": []},
        call_id="patch-malformed-preview",
    )

    presentation.emit.assert_not_awaited()


@pytest.mark.anyio
async def test_permission_tool_has_no_generic_tool_result_display() -> None:
    presentation = SimpleNamespace(emit=AsyncMock())
    result = ToolExecutionResult(
        ok=True,
        fields={"ok": True},
        text="permissions granted",
        data={},
        hook_response={},
        cost_ms=1,
        status="completed",
    )

    await show_tool_start(
        presentation,
        "request_permissions",
        {"permissions": {"network": {"enabled": True}}},
    )
    await show_tool_result(
        presentation,
        "request_permissions",
        {"permissions": {"network": {"enabled": True}}},
        result,
    )

    presentation.emit.assert_not_awaited()


@pytest.mark.anyio
async def test_durable_tool_uses_journal_before_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    order = []

    class Journal:
        async def inspect(self, effect):
            assert effect.effect_id == "effect_client_call"
            order.append("journal_inspect")
            return EffectJournalDecision("execute")

        async def begin(self, effect):
            assert effect.effect_id == "effect_client_call"
            order.append("journal_begin")
            return EffectJournalDecision("execute")

        async def commit(self, effect, result_payload):
            _ = effect, result_payload
            order.append("journal_commit")

        async def mark_unknown(self, effect, error):
            _ = effect, error
            order.append("journal_unknown")

    async def run_allowed(invocation, operation):
        order.append("hooks")
        operation_result = await operation(invocation)
        return ToolCallRunResult(
            allowed=True,
            value=operation_result.value,
            visible_result=HookVisibleToolResult(
                ok=True,
                text="done",
                fields=_compact_fields(),
            ),
        )

    coordinator = SimpleNamespace(run_invocation=AsyncMock(side_effect=run_allowed))
    runner, _ = _runner(
        coordinator,
        effect_journal=Journal(),
    )

    async def execute_tool(*args, **kwargs):
        _ = args, kwargs
        order.append("tool")
        return SimpleNamespace(
            ok=True,
            fields=_compact_fields(),
            text="done",
            cost_ms=1,
        )

    runner.tool_execution.execute = execute_tool
    monkeypatch.setattr(client_call, "show_tool_start", AsyncMock())
    monkeypatch.setattr(client_call, "show_tool_result", AsyncMock())

    outcome = await runner.execute(
        _durable_invocation(tmp_path),
        use_coding_trace=False,
    )

    assert outcome.result.ok is True
    assert order == [
        "journal_inspect",
        "journal_begin",
        "hooks",
        "tool",
        "journal_commit",
    ]


@pytest.mark.anyio
async def test_committed_local_effect_reuses_outcome_without_hooks_or_display(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def run_allowed(invocation, operation):
        operation_result = await operation(invocation)
        return ToolCallRunResult(
            allowed=True,
            value=operation_result.value,
            visible_result=HookVisibleToolResult(
                ok=True,
                text="done",
                fields=_compact_fields(),
            ),
        )

    coordinator = SimpleNamespace(run_invocation=AsyncMock(side_effect=run_allowed))
    journal = open_effect_journal(tmp_path / "effects.db")
    runner, ports = _runner(
        coordinator,
        effect_journal=journal,
    )
    tool_run = SimpleNamespace(
        ok=True,
        fields=_compact_fields(),
        text="done",
        cost_ms=1,
    )
    run_tool = AsyncMock(return_value=tool_run)
    show_start = AsyncMock()
    show_result = AsyncMock()
    runner.tool_execution.execute = run_tool
    monkeypatch.setattr(client_call, "show_tool_start", show_start)
    monkeypatch.setattr(client_call, "show_tool_result", show_result)
    invocation = _durable_invocation(tmp_path)

    first = await runner.execute(invocation, use_coding_trace=False)
    second = await runner.execute(invocation, use_coding_trace=False)

    assert second.result.name == first.result.name
    assert second.result.arguments == first.result.arguments
    assert second.result.ok == first.result.ok
    assert second.result.text == first.result.text
    assert second.result.fields == first.result.fields
    assert second.additional_context == first.additional_context
    assert coordinator.run_invocation.await_count == 1
    assert run_tool.await_count == 1
    assert ports.output.record_tool_arguments.call_count == 1
    assert show_start.await_count == 1
    assert show_result.await_count == 1


@pytest.mark.anyio
async def test_custom_operation_uses_same_durable_effect_boundary(
    tmp_path: Path,
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
                additional_context=operation_result.additional_context,
            ),
        )

    coordinator = SimpleNamespace(run_invocation=AsyncMock(side_effect=run_allowed))
    runner, _ports = _runner(
        coordinator,
        effect_journal=open_effect_journal(tmp_path / "effects.db"),
    )
    operation = Mock()

    async def execute_plan(invocation):
        result = ClientToolCallResult(
            name=invocation.name,
            arguments=dict(invocation.arguments),
            ok=True,
            text="plan complete",
            cost_ms=4,
            call_id=invocation.call_id,
            fields=_compact_fields(text="plan complete"),
        )
        operation(invocation)
        return ToolOperationResult(
            value=result,
            snapshot=ToolResultSnapshot(
                ok=True,
                text=result.text,
                fields=result.fields,
            ),
            additional_context=("nested context",),
        )

    invocation = _durable_invocation(tmp_path)
    first = await runner.execute(
        invocation,
        use_coding_trace=False,
        operation_handler=execute_plan,
    )
    second = await runner.execute(
        invocation,
        use_coding_trace=False,
        operation_handler=execute_plan,
    )

    assert first.result.text == "plan complete"
    assert second.result.text == "plan complete"
    assert second.additional_context == ("nested context",)
    assert operation.call_count == 1
    assert coordinator.run_invocation.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("reconcile_succeeds", (True, False))
async def test_local_effect_commit_failure_uses_control_plane_reconciliation(
    tmp_path: Path,
    monkeypatch,
    reconcile_succeeds: bool,
) -> None:
    journal = SimpleNamespace(
        inspect=AsyncMock(return_value=EffectJournalDecision("execute")),
        begin=AsyncMock(return_value=EffectJournalDecision("execute")),
        commit=AsyncMock(side_effect=OSError("disk unavailable")),
        mark_unknown=AsyncMock(),
    )
    reconciler = AsyncMock(
        return_value={"ok": True, "status": "reconciled", "effect": {}}
    )
    if not reconcile_succeeds:
        reconciler.side_effect = OSError("control plane unavailable")

    async def run_allowed(invocation, operation):
        operation_result = await operation(invocation)
        return ToolCallRunResult(
            allowed=True,
            value=operation_result.value,
            visible_result=HookVisibleToolResult(
                ok=True,
                text="done",
                fields=_compact_fields(),
            ),
        )

    coordinator = SimpleNamespace(run_invocation=AsyncMock(side_effect=run_allowed))
    runner, _ = _runner(
        coordinator,
        effect_journal=journal,
        effect_reconciler=reconciler,
    )
    runner.tool_execution.execute = AsyncMock(return_value=SimpleNamespace(
        ok=True,
        fields=_compact_fields(),
        text="done",
        cost_ms=1,
    ))
    monkeypatch.setattr(client_call, "show_tool_start", AsyncMock())
    monkeypatch.setattr(client_call, "show_tool_result", AsyncMock())

    if reconcile_succeeds:
        outcome = await runner.execute(
            _durable_invocation(tmp_path),
            use_coding_trace=False,
        )
        assert outcome.result.text == "done"
        assert journal.commit.await_count == 2
    else:
        with pytest.raises(LocalEffectReconciliationRequired) as captured:
            await runner.execute(
                _durable_invocation(tmp_path),
                use_coding_trace=False,
            )
        assert captured.value.effect_id == "effect_client_call"
        assert journal.commit.await_count == 1

    journal.mark_unknown.assert_awaited_once()
    persisted = journal.mark_unknown.await_args.kwargs["result_payload"]
    assert {
        key: value
        for key, value in persisted.items()
        if key != "reconciliation_result_payload"
    } == {
        "name": "test_tool",
        "arguments": {"value": 1},
        "ok": True,
        "text": "done",
        "cost_ms": 1,
        "call_id": "call-1",
        "fields": {
            "ok": True,
            "tool": "test_tool",
            "source": "client",
            "args": {"value": 1},
            "text": "done",
            "attachments": [],
            "data": {},
        },
        "additional_context": [],
    }
    assert persisted["reconciliation_result_payload"]["call_id"] == "call-1"
    reconciler.assert_awaited_once()
    reconcile_payload = reconciler.await_args.kwargs
    assert reconcile_payload["effect_id"] == "effect_client_call"
    assert reconcile_payload["resolution"] == "committed"
    assert reconcile_payload["result_payload"]["call_id"] == "call-1"
    assert reconcile_payload["result_payload"]["result"]["tool"] == "test_tool"
    assert "execution" not in reconcile_payload["result_payload"]


@pytest.mark.anyio
async def test_known_local_result_recovers_server_reconciliation_pause() -> None:
    result_payload = {
        "request_id": "effect-result-test",
    }
    journal = SimpleNamespace(
        reconciliation_result=AsyncMock(return_value=result_payload),
        mark_reconciled=AsyncMock(),
    )
    reconciler = AsyncMock(return_value={
        "ok": True,
        "status": "reconciled",
        "effect": {},
    })
    runner, _ = _runner(
        SimpleNamespace(),
        effect_journal=journal,
        effect_reconciler=reconciler,
    )

    assert await runner.reconcile_known_effect("effect_client_call") is True
    assert reconciler.await_args.kwargs["result_payload"] == result_payload
    assert reconciler.await_args.kwargs["resolution"] == "committed"
    journal.mark_reconciled.assert_awaited_once_with("effect_client_call")


@pytest.mark.anyio
async def test_known_unexecuted_result_reconciles_as_failed() -> None:
    result_payload = {
        "result": {"data": {"executed": False}},
    }
    journal = SimpleNamespace(
        reconciliation_result=AsyncMock(return_value=result_payload),
        mark_reconciled=AsyncMock(),
    )
    reconciler = AsyncMock(return_value={"ok": True})
    runner, _ = _runner(
        SimpleNamespace(),
        effect_journal=journal,
        effect_reconciler=reconciler,
    )

    assert await runner.reconcile_known_effect("effect_client_call") is True
    assert reconciler.await_args.kwargs["resolution"] == "failed"
    assert reconciler.await_args.kwargs["error"]


@pytest.mark.anyio
async def test_known_effect_ignores_only_journal_persistence_failure() -> None:
    result_payload = {
        "request_id": "effect-result-test",
    }
    journal = SimpleNamespace(
        reconciliation_result=AsyncMock(return_value=result_payload),
        mark_reconciled=AsyncMock(side_effect=EffectJournalPersistenceError()),
    )
    runner, _ = _runner(
        SimpleNamespace(),
        effect_journal=journal,
        effect_reconciler=AsyncMock(return_value={"ok": True}),
    )

    assert await runner.reconcile_known_effect("effect_client_call") is True

    journal.mark_reconciled.side_effect = ValueError("identity mismatch")
    with pytest.raises(ValueError, match="identity mismatch"):
        await runner.reconcile_known_effect("effect_client_call")


@pytest.mark.anyio
async def test_client_tool_call_rejects_tool_outside_turn_catalog(monkeypatch) -> None:
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
        call_id="call-hidden",
        name="hidden_tool",
        arguments={},
    )
    monkeypatch.setattr(client_call, "show_tool_result", AsyncMock())
    runner.session.call_tool = AsyncMock()

    outcome = await runner.execute(invocation, use_coding_trace=False)

    assert outcome.result.ok is False
    assert "tool is unavailable in this turn: hidden_tool" in outcome.result.text
    runner.session.call_tool.assert_not_awaited()


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
    runner, ports = _runner(coordinator, tools=[{"name": "js_repl"}])
    base = _invocation()
    invocation = ToolInvocation(
        turn=base.turn,
        call_id="call-js",
        name="js_repl",
        arguments={"code": "await work();", "timeout_ms": 30000},
    )
    tool_run = SimpleNamespace(
        ok=True,
        fields=_compact_fields(),
        text="done",
        cost_ms=200,
    )
    run_tool_step = AsyncMock(return_value=tool_run)
    runner.tool_execution.execute = run_tool_step
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
    ("name", "use_coding_trace", "shows_start"),
    (
        ("shell_command", True, True),
        ("apply_patch", True, True),
        ("exec_command", True, False),
        ("write_stdin", True, False),
        ("js_repl_reset", True, False),
        ("mcp__docs__search", False, True),
        ("view_image", False, False),
    ),
)
async def test_tool_start_trace_uses_two_stage_policy(
    monkeypatch,
    name: str,
    use_coding_trace: bool,
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
    runner, _ports = _runner(coordinator, tools=[{"name": name}])
    base = _invocation()
    invocation = ToolInvocation(
        turn=base.turn,
        call_id=f"call-{name}",
        name=name,
        arguments={"command": "echo ready"},
    )
    tool_run = SimpleNamespace(
        ok=True,
        fields=_compact_fields(),
        text="done",
        cost_ms=7,
    )
    show_start = AsyncMock()
    runner.tool_execution.execute = AsyncMock(return_value=tool_run)
    monkeypatch.setattr(client_call, "show_tool_start", show_start)
    monkeypatch.setattr(client_call, "show_tool_result", AsyncMock())

    await runner.execute(invocation, use_coding_trace=use_coding_trace)

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
    runner.tool_execution.execute = AsyncMock(
        side_effect=RuntimeError("failed")
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
    runner.tool_execution.execute = run_tool_step

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
                    "attachments": [],
                    "data": {"redacted": True},
                },
                additional_context=("review replacement",),
            ),
        )

    coordinator = SimpleNamespace(run_invocation=AsyncMock(side_effect=run_allowed))
    runner, _ports = _runner(coordinator)
    tool_run = SimpleNamespace(
        ok=True,
        fields=_compact_fields(text="original"),
        text="original",
        cost_ms=3,
    )
    runner.tool_execution.execute = AsyncMock(return_value=tool_run)
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


def test_client_tool_outcome_rejects_invalid_result() -> None:
    invalid_result: typing.Any = SimpleNamespace()
    with pytest.raises(
        TypeError,
        match="must return ClientToolCallResult",
    ):
        ClientToolCallOutcome(
            result=invalid_result
        )


def test_client_tool_result_has_no_hook_feedback_fields() -> None:
    result = client_call.ClientToolCallResult(
        name="test_tool",
        arguments={},
        ok=True,
        text="done",
        fields={
            "ok": True,
            "tool": "test_tool",
            "source": "client",
            "args": {},
            "text": "done",
            "attachments": [],
            "data": {},
        },
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
