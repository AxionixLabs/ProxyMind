# -*- coding: utf-8 -*-

"""验证 Pre/PostToolUse 与工具执行结果的因果边界。"""


import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
import infrastructure.platform.hook_command as hook_command_module
import agent.harness.hooks.runtime as hook_runtime_module
from agent.application.turns.context import (
    AgentContext,
    ToolInvocation,
    TurnContext
)
from infrastructure.platform.hook_command import (
    HookCommandError,
    HookCommandExecutor,
    HookCommandOutput,
)
from agent.application.hooks.events import HOOK_EVENT_SPECS
from agent.application.hooks.models import (
    HookEventRequest,
    HookOutputEntry,
    ToolOperationResult,
    ToolResultSnapshot
)
from infrastructure.platform.hook_output_spill import HookOutputSpillStore
from agent.harness.hooks.registry import HookRegistry
from agent.harness.hooks.runtime import HookRuntime
from agent.harness.hooks.async_tasks import HookAsyncTaskOwner
from agent.application.hooks.context import HookExecutionContext
from agent.harness.hooks.scope import HookExecutionScope
from agent.harness.hooks.session_lifecycle import SessionLifecycleGateway
from agent.harness.hooks.tool_lifecycle import (
    CommandHookSessionStore,
    ToolCallCoordinator,
    ToolHookEvents
)
from agent.harness.hooks.turn_lifecycle import (
    PromptHookBlockedError,
    TurnHookEvents
)
from agent.application.views.builders.approval import build_approval_view
from frontends.terminal.renderers.approval import render_approval_view
from infrastructure.hooks.discovery import resolve_hook_definitions
from agent.domain.hooks import HOOK_EVENT_NAMES
from agent.domain.policies import preset_permissions


class _CommandRunner:
    def __init__(self, outputs=None, errors=None) -> None:
        self.outputs = dict(outputs or {})
        self.errors = dict(errors or {})
        self.calls = []

    async def execute(self, definition, payload):
        self.calls.append((definition, payload))
        error = self.errors.get(definition.key)
        if error is not None:
            raise error
        return HookCommandOutput(data=_wire_output(
            definition.event,
            self.outputs.get(definition.key),
        ))


def _wire_output(event, output):
    """把测试用例的语义输出写成当前 Hook wire 格式。"""
    data = dict(output or {})
    specific = dict(data.get("hookSpecificOutput") or {})

    if event == "PermissionRequest" and isinstance(data.get("decision"), str):
        message = data.pop("reason", "")
        specific.update({
            "hookEventName": event,
            "decision": {
                "behavior": data.pop("decision"),
                **({"message": message} if message else {}),
            },
        })
    elif event == "PreToolUse":
        decision = data.pop("decision", None)
        reason = data.pop("reason", "")
        if decision == "deny":
            specific.update({
                "hookEventName": event,
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            })
        elif decision not in {None, "allow"}:
            data["decision"] = decision
        updated_input = data.pop("updatedInput", None)
        if updated_input is not None:
            specific.update({
                "hookEventName": event,
                "permissionDecision": specific.get("permissionDecision", "allow"),
                "updatedInput": updated_input,
            })
    if event in {
        "SessionStart",
        "UserPromptSubmit",
        "SubagentStart",
        "PreToolUse",
        "PostToolUse",
    }:
        context = data.pop("additionalContext", None)
        if context is not None:
            if isinstance(context, list):
                context = "\n\n".join(context)
            specific.update({
                "hookEventName": event,
                "additionalContext": context,
            })

    data.pop("additional_context", None)
    data.pop("hookSpecificOutput", None)
    if specific:
        data["hookSpecificOutput"] = specific
    if event in {"Stop", "SubagentStop"}:
        continuation = data.pop("continuationPrompt", None)
        if continuation is not None:
            data["decision"] = "block"
            data["reason"] = continuation
        context = data.pop("additionalContext", None)
        if context is not None:
            data["systemMessage"] = data.get("systemMessage", "")
    return data


def _definitions(raw):
    return resolve_hook_definitions(
        raw,
        source_scope="user",
        source_path=Path("config.toml"),
    )


def _hook(
    command,
    *,
    matcher=None,
    timeout=None,
    command_windows=None,
    status_message=None,
    run_async=False,
    additional_context_limit=None,
):
    handler = {"type": "command", "command": command}
    if timeout is not None:
        handler["timeout"] = timeout
    if command_windows is not None:
        handler["commandWindows"] = command_windows
    if status_message is not None:
        handler["statusMessage"] = status_message
    if run_async:
        handler["async"] = True
    if additional_context_limit is not None:
        handler["additionalContextLimit"] = additional_context_limit
    config = {"hooks": [handler]}
    if matcher is not None:
        config["matcher"] = matcher
    return config


def _invocation(
    *,
    turn_id: str = "turn_test",
    session_started: bool = False,
    model: str = "test-model",
    cwd: str = ".",
) -> ToolInvocation:
    turn = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        source="test",
        pref_config={"primary": {"model": model}},
        cwd=cwd,
        permissions=preset_permissions("auto"),
        turn_id=turn_id,
        session_started=session_started,
        session_start_reason="initial" if session_started else "",
    )
    return ToolInvocation(
        turn=turn,
        call_id="call_test",
        name="shell_command",
        arguments={"command": "rg TODO"},
        meta={"domain": "coding"},
    )


def _scope(
    runtime: HookRuntime,
    invocation: ToolInvocation | None = None,
) -> HookExecutionScope:
    invocation = invocation or _invocation()
    return HookExecutionScope(
        context=HookExecutionContext.from_turn(invocation.turn),
        dispatcher=runtime,
    )


@pytest.mark.anyio
async def test_tool_coordinator_reuses_prepared_decision_and_runs_post() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("pre")],
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner()
    transcript_entries = []

    def record_transcript(event, *, actor=None, payload=None) -> None:
        transcript_entries.append((event, actor, dict(payload or {})))

    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner)),
        transcript=SimpleNamespace(append=record_transcript),
    )
    invocation = _invocation()

    decision = await coordinator.prepare(invocation)
    assert [entry[0] for entry in transcript_entries] == ["tool.started"]
    result = await coordinator.run_invocation(
        invocation,
        lambda _prepared: _return_value(SimpleNamespace(
            ok=True,
            text="done",
            fields={"ok": True, "data": {"answer": 42}},
        )),
    )

    assert decision.allowed
    assert result.allowed
    assert result.value.text == "done"
    assert [call[0].event for call in runner.calls] == [
        "PreToolUse",
        "PostToolUse",
    ]
    assert runner.calls[1][1]["tool_response"]["ok"] is True
    assert runner.calls[1][1]["tool_response"]["data"] == {
        "answer": 42,
    }
    assert [entry[0] for entry in transcript_entries] == [
        "tool.started",
        "tool.completed",
    ]


@pytest.mark.anyio
async def test_apply_patch_transcript_start_carries_preview_delta() -> None:
    transcript_entries = []

    def record_transcript(event, *, actor=None, payload=None) -> None:
        transcript_entries.append((event, actor, dict(payload or {})))

    base = _invocation()
    invocation = replace(
        base,
        name="apply_patch",
        arguments={"patch": "patch"},
    )
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(_definitions({}), command_runner=_CommandRunner()), invocation),
        transcript=SimpleNamespace(append=record_transcript),
    )

    decision = await coordinator.prepare(invocation)
    assert decision.allowed
    assert transcript_entries == []

    preview = {"files": [], "delta": {"exact": True, "changes": []}}
    coordinator.record_patch_start(invocation, preview_data=preview)

    assert transcript_entries == [(
        "tool.started",
        "tool",
        {
            "call_id": "call_test",
            "name": "apply_patch",
            "arguments": {"patch": "patch"},
            "patch_preview": preview,
        },
    )]


@pytest.mark.anyio
async def test_exec_command_post_waits_for_terminal_write_stdin_poll() -> None:
    start_definitions = _definitions({
        "PreToolUse": [_hook("pre", matcher="Bash")],
    })
    finish_definitions = _definitions({
        "PostToolUse": [_hook("post", matcher="Bash")],
    })
    start_runner = _CommandRunner()
    finish_runner = _CommandRunner(outputs={
        finish_definitions[0].key: {
            "additionalContext": "review final output",
        },
    })
    start_runtime = HookRuntime(
        start_definitions,
        command_runner=start_runner,
    )
    finish_runtime = HookRuntime(
        finish_definitions,
        command_runner=finish_runner,
    )
    sessions = CommandHookSessionStore()

    original = _invocation(
        turn_id="turn_original",
        model="start-model",
        cwd="/start",
    )
    exec_invocation = ToolInvocation(
        turn=original.turn,
        call_id="exec_call",
        name="exec_command",
        arguments={"command": "long-running-command"},
        meta=original.meta,
    )
    exec_coordinator = ToolCallCoordinator(
        _scope(start_runtime, exec_invocation),
        command_sessions=sessions,
    )

    poll_base = _invocation(
        turn_id="turn_poll",
        model="finish-model",
        cwd="/finish",
    )

    def write_invocation(call_id: str) -> ToolInvocation:
        return ToolInvocation(
            turn=poll_base.turn,
            call_id=call_id,
            name="write_stdin",
            arguments={"session_id": "exec_session", "stdin": ""},
            meta=poll_base.meta,
        )

    poll_coordinator = ToolCallCoordinator(
        _scope(finish_runtime, poll_base),
        command_sessions=sessions,
    )

    async def command_result(
        status: str,
        response: str,
        *,
        ok: bool = True,
        exit_code: int | None = None,
    ):
        fields = {
            "ok": ok,
            "status": status,
            "session_id": "exec_session",
            "data": {
                "status": status,
                "session_id": "exec_session",
                "exit_code": exit_code,
            },
        }
        value = SimpleNamespace(ok=ok, text=response, fields=fields)
        return ToolOperationResult(
            value=value,
            snapshot=ToolResultSnapshot(
                ok=ok,
                text=response,
                fields=fields,
            ),
            hook_response=response,
        )

    started = await exec_coordinator.run_invocation(
        exec_invocation,
        lambda _prepared: command_result("running", "partial output"),
    )
    polled = await poll_coordinator.run_invocation(
        write_invocation("poll_call"),
        lambda _prepared: command_result("running", "more partial output"),
    )
    completed = await poll_coordinator.run_invocation(
        write_invocation("final_call"),
        lambda _prepared: command_result(
            "exited",
            "failed command output",
            ok=False,
            exit_code=2,
        ),
    )
    await poll_coordinator.run_invocation(
        write_invocation("duplicate_call"),
        lambda _prepared: command_result("exited", "duplicate output"),
    )

    assert started.visible_result.additional_context == ()
    assert polled.visible_result.additional_context == ()
    assert completed.visible_result.additional_context == (
        "review final output",
    )
    assert [call[0].event for call in start_runner.calls] == ["PreToolUse"]
    assert [call[0].event for call in finish_runner.calls] == ["PostToolUse"]
    post_payload = finish_runner.calls[0][1]
    assert post_payload["tool_name"] == "Bash"
    assert post_payload["tool_use_id"] == "exec_call"
    assert post_payload["tool_input"] == {
        "command": "long-running-command",
    }
    assert post_payload["tool_response"] == "failed command output"
    assert post_payload["turn_id"] == "turn_poll"
    assert post_payload["model"] == "finish-model"
    assert post_payload["cwd"] == "/finish"


@pytest.mark.anyio
async def test_tool_coordinator_uses_explicit_hook_response() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner()
    coordinator = ToolCallCoordinator(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    )))

    async def operation(_invocation):
        return ToolOperationResult(
            value="done",
            snapshot=ToolResultSnapshot(
                ok=True,
                text="model output",
                fields={"ok": True, "data": {"normalized": True}},
            ),
            hook_response="hook output",
        )

    result = await coordinator.run_invocation(_invocation(), operation)

    assert result.allowed
    assert runner.calls[0][1]["tool_response"] == "hook output"


@pytest.mark.anyio
@pytest.mark.parametrize("hook_response", [
    "x" * 40000,
    {
        "content": [{"type": "text", "text": "x" * 40000}],
        "structuredContent": {"answer": 42},
        "isError": False,
    },
], ids=("text", "mcp"))
async def test_tool_coordinator_preserves_large_hook_response_shape(
    hook_response,
) -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner()
    coordinator = ToolCallCoordinator(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    )))

    async def operation(_invocation):
        return ToolOperationResult(
            value="done",
            snapshot=ToolResultSnapshot(
                ok=True,
                text="model output",
                fields={"ok": True},
            ),
            hook_response=hook_response,
        )

    await coordinator.run_invocation(_invocation(), operation)

    assert runner.calls[0][1]["tool_response"] == hook_response


@pytest.mark.anyio
async def test_tool_coordinator_skips_post_for_failed_result() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner()
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )

    result = await coordinator.run_invocation(
        _invocation(),
        lambda _prepared: _return_value(SimpleNamespace(
            ok=False,
            text="failed",
            fields={"ok": False, "error": "failed"},
        )),
    )

    assert result.allowed
    assert result.visible_result.ok is False
    assert runner.calls == []


@pytest.mark.anyio
async def test_shell_nonzero_exit_runs_post_tool_use() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("post", matcher="Bash")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "additionalContext": "inspect failed test output",
        },
    })
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )
    fields = {
        "ok": False,
        "text": "shell_command failed exit_code=1",
        "data": {
            "exit_code": 1,
            "stdout": "FAILED tests/test_sample.py",
            "stderr": "assert 1 == 2",
        },
    }

    result = await coordinator.run_invocation(
        _invocation(),
        lambda _prepared: _return_value(SimpleNamespace(
            ok=False,
            text=fields["text"],
            fields=fields,
        )),
    )

    assert result.allowed
    assert result.visible_result.ok is False
    assert result.visible_result.additional_context == (
        "inspect failed test output",
    )
    assert [call[0].event for call in runner.calls] == ["PostToolUse"]
    assert runner.calls[0][1]["tool_response"] == fields


@pytest.mark.anyio
async def test_tool_coordinator_skips_post_after_operation_error() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("pre")],
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "additionalContext": "inspect protected paths",
        },
    })
    transcript_entries = []
    failure_context = []

    def record_transcript(event, *, actor=None, payload=None) -> None:
        transcript_entries.append((event, actor, dict(payload or {})))

    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner)),
        transcript=SimpleNamespace(append=record_transcript),
        failure_context_sink=failure_context.extend,
    )

    async def fail(_prepared):
        raise RuntimeError("tool failed")

    with pytest.raises(RuntimeError, match="tool failed"):
        await coordinator.run_invocation(_invocation(), fail)

    assert [call[0].event for call in runner.calls] == ["PreToolUse"]
    assert failure_context == ["inspect protected paths"]
    assert [entry[0] for entry in transcript_entries] == [
        "tool.started",
        "tool.failed",
    ]
    assert transcript_entries[1][2]["error"] == "RuntimeError: tool failed"


@pytest.mark.anyio
async def test_tool_coordinator_skips_post_after_operation_cancellation() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("pre")],
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "additionalContext": "preserve cancellation policy",
        },
    })
    transcript_entries = []
    failure_context = []

    def record_transcript(event, *, actor=None, payload=None) -> None:
        transcript_entries.append((event, actor, dict(payload or {})))

    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner)),
        transcript=SimpleNamespace(append=record_transcript),
        failure_context_sink=failure_context.extend,
    )

    async def cancel(_prepared):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await coordinator.run_invocation(_invocation(), cancel)

    assert [call[0].event for call in runner.calls] == ["PreToolUse"]
    assert failure_context == ["preserve cancellation policy"]
    assert [entry[0] for entry in transcript_entries] == [
        "tool.started",
        "tool.failed",
    ]
    assert transcript_entries[1][2]["cancelled"] is True


@pytest.mark.anyio
async def test_pre_tool_use_updated_input_reaches_execution_and_post_hook() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("pre")],
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "decision": "allow",
            "updatedInput": {"command": "pytest -q"},
        },
    })
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )
    prepared_invocations = []

    async def operation(prepared):
        prepared_invocations.append(prepared)
        return _operation_result(SimpleNamespace(
            ok=True,
            text="done",
            fields={"ok": True, "data": {"answer": 42}},
        ))

    decision = await coordinator.prepare(_invocation())
    effective = coordinator.effective_invocation(_invocation(), decision)
    result = await coordinator.run_invocation(effective, operation)

    assert result.allowed
    assert prepared_invocations[0].arguments == {"command": "pytest -q"}
    assert [call[0].event for call in runner.calls] == [
        "PreToolUse",
        "PostToolUse",
    ]
    assert runner.calls[1][1]["tool_input"] == {"command": "pytest -q"}


@pytest.mark.anyio
async def test_apply_patch_uses_command_wire_input_and_rewrites_patch() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("rewrite", matcher="apply_patch")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "updatedInput": {"command": "*** Begin Patch\n*** End Patch"},
        },
    })
    original = _invocation()
    invocation = ToolInvocation(
        turn=original.turn,
        call_id="patch_call",
        name="apply_patch",
        arguments={"patch": "old", "force": False},
    )
    coordinator = ToolCallCoordinator(_scope(
        HookRuntime(definitions, command_runner=runner),
        invocation,
    ))

    decision = await coordinator.prepare(invocation)
    effective = coordinator.effective_invocation(invocation, decision)

    assert runner.calls[0][1]["tool_name"] == "apply_patch"
    assert runner.calls[0][1]["tool_input"] == {"command": "old"}
    assert effective.arguments == {
        "patch": "*** Begin Patch\n*** End Patch",
        "force": False,
    }


@pytest.mark.anyio
async def test_pre_tool_use_context_reaches_tool_run_result() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("pre")],
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "additionalContext": "prefer concise output",
            "systemMessage": "Treat the tool result as summarized.",
        },
        definitions[1].key: {
            "additionalContext": "post context",
            "systemMessage": "Post message.",
        },
    })
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )

    result = await coordinator.run_invocation(
        _invocation(),
        lambda _prepared: _return_value(SimpleNamespace(
            ok=True,
            text="done",
            fields={"ok": True, "data": {"answer": 42}},
        )),
    )

    assert result.allowed
    assert result.visible_result.additional_context == (
        "prefer concise output",
        "post context",
    )


@pytest.mark.anyio
async def test_post_tool_use_ignores_unsupported_result_rewrite() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "replacementResult": {
                "ok": False,
                "text": "redacted",
                "data": {"redacted": True},
            },
            "additionalContext": ["explain the redaction"],
            "systemMessage": "Do not reveal the original output.",
        },
    })
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )

    result = await coordinator.run_invocation(
        _invocation(),
        lambda _prepared: _return_value(SimpleNamespace(
            ok=True,
            text="secret",
            fields={"ok": True, "data": {"secret": "value"}},
        )),
    )

    assert result.visible_result.text == "secret"
    assert result.visible_result.fields["data"] == {"secret": "value"}
    assert result.visible_result.additional_context == ()


@pytest.mark.anyio
async def test_post_tool_use_stop_returns_feedback_without_blocking() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "continue": False,
            "stopReason": "stop processing hook output",
            "reason": "review the tool result",
        },
    })
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )

    original = SimpleNamespace(
        ok=True,
        text="original",
        fields={"ok": True, "data": {"answer": 42}},
    )
    result = await coordinator.run_invocation(
        _invocation(),
        lambda _prepared: _return_value(original),
    )

    assert result.allowed
    assert result.value is original
    assert result.visible_result.ok is True
    assert result.visible_result.text == "review the tool result"
    assert result.visible_result.fields == {
        "ok": True,
        "text": "review the tool result",
        "attachments": [],
        "data": {"hook_feedback": True},
    }


@pytest.mark.anyio
async def test_post_tool_use_rewrite_cannot_bypass_original_result() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "replacementResult": {
                "ok": True,
                "text": "replacement",
            },
        },
    })
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )

    original = SimpleNamespace(
        ok=True,
        text="original",
        fields={"ok": True, "data": {"answer": 42}},
    )
    result = await coordinator.run_invocation(
        _invocation(),
        lambda _prepared: _return_value(original),
    )

    assert result.allowed
    assert result.value is original
    assert result.visible_result.ok is True
    assert result.visible_result.text == "original"
    assert result.visible_result.fields == original.fields


@pytest.mark.anyio
async def test_post_tool_use_failure_does_not_replace_tool_result() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("broken-post")],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("audit failed"),
    })
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )

    result = await coordinator.run_invocation(
        _invocation(),
        lambda _prepared: _return_value("done"),
    )

    assert result.allowed
    assert result.value == "done"


async def _return_value(value):
    return _operation_result(value)


def _operation_result(value):
    fields = getattr(value, "fields", None)
    if isinstance(fields, dict):
        ok = bool(getattr(value, "ok", True))
        text = str(getattr(value, "text", "") or "")
        snapshot_fields = fields
    else:
        ok = True
        text = str(value or "")
        snapshot_fields = {"ok": True, "text": text}

    return ToolOperationResult(
        value=value,
        snapshot=ToolResultSnapshot(
            ok=ok,
            text=text,
            fields=snapshot_fields,
        ),
    )
