# -*- coding: utf-8 -*-

"""验证 Session、Turn、Prompt 与 Stop hook 的有序生命周期。"""


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
async def test_scope_owns_common_payload_fields() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("check")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"decision": "allow"},
    })
    scope = _scope(HookRuntime(definitions, command_runner=runner))

    await scope.dispatch(
        "PreToolUse",
        payload={
            "session_id": "spoofed",
            "turn_id": "spoofed",
            "tool_use_id": "call_test",
            "tool_name": "shell_command",
            "tool_input": {"command": "rg TODO"},
        },
        match_value="shell_command",
    )

    payload = runner.calls[0][1]
    assert payload["session_id"] == "sid_test"
    assert payload["turn_id"] == "turn_test"
    assert payload["tool_use_id"] == "call_test"
    assert payload["hook_event_name"] == "PreToolUse"
    assert "root_session_id" not in payload
    assert "conversation_id" not in payload
    assert "agent_id" not in payload


@pytest.mark.anyio
async def test_turn_hooks_dispatch_start_prompt_and_stop_in_order() -> None:
    definitions = _definitions({
        "SessionStart": [_hook("start", matcher="startup")],
        "UserPromptSubmit": [_hook("prompt")],
        "Stop": [_hook("stop")],
    })
    runner = _CommandRunner(outputs={
        definitions[1].key: {"continue": True},
    })
    events = TurnHookEvents(_scope(
        HookRuntime(definitions, command_runner=runner),
        _invocation(session_started=True),
    ))

    await events.begin("hello")
    await events.stop(outcome="completed", usage={"output_tokens": 3})

    assert [call[0].event for call in runner.calls] == [
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
    ]
    assert runner.calls[0][1]["source"] == "startup"
    assert runner.calls[1][1]["prompt"] == "hello"
    assert runner.calls[2][1]["stop_hook_active"] is False
    assert runner.calls[2][1]["last_assistant_message"] is None


@pytest.mark.anyio
async def test_interrupt_hook_dispatches_for_root_and_reports_warning() -> None:
    definitions = _definitions({
        "Interrupt": [_hook("interrupt")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"systemMessage": "turn cancelled"},
    })
    scope = _scope(HookRuntime(definitions, command_runner=runner))

    await TurnHookEvents(scope).interrupt()

    assert [call[0].event for call in runner.calls] == ["Interrupt"]
    assert runner.calls[0][1]["hook_event_name"] == "Interrupt"
    assert runner.calls[0][1]["turn_id"] == "turn_test"


@pytest.mark.anyio
async def test_interrupt_hook_skips_subagent_scope() -> None:
    root = _invocation().turn.agent
    child_turn = replace(
        _invocation().turn,
        agent=root.child("worker", "task"),
    )
    definitions = _definitions({
        "Interrupt": [_hook("interrupt")],
    })
    runner = _CommandRunner()
    scope = HookExecutionScope(
        context=HookExecutionContext.from_turn(child_turn),
        dispatcher=HookRuntime(definitions, command_runner=runner),
    )

    await TurnHookEvents(scope).interrupt()

    assert runner.calls == []


@pytest.mark.anyio
async def test_turn_hooks_skip_session_start_for_existing_session() -> None:
    definitions = _definitions({
        "SessionStart": [_hook("start", matcher="startup")],
        "UserPromptSubmit": [_hook("prompt")],
    })
    runner = _CommandRunner()
    events = TurnHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    )))

    await events.begin("hello")

    assert [call[0].event for call in runner.calls] == ["UserPromptSubmit"]


@pytest.mark.anyio
async def test_session_start_continue_false_stops_turn_start() -> None:
    definitions = _definitions({
        "SessionStart": [_hook("start", matcher="startup")],
        "UserPromptSubmit": [_hook("prompt")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "continue": False,
            "stopReason": "startup stopped",
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "Python 3.13 is required",
            },
        },
    })
    events = TurnHookEvents(_scope(
        HookRuntime(definitions, command_runner=runner),
        _invocation(session_started=True),
    ))

    with pytest.raises(PromptHookBlockedError, match="startup stopped") as caught:
        await events.begin("hello")

    assert caught.value.additional_context == ("Python 3.13 is required",)
    assert [call[0].event for call in runner.calls] == ["SessionStart"]


@pytest.mark.anyio
async def test_turn_prompt_hook_failure_does_not_block() -> None:
    definitions = _definitions({
        "UserPromptSubmit": [_hook("broken")],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("prompt hook failed"),
    })
    events = TurnHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    )))

    result = await events.begin("hello")

    assert result.message == "hello"


@pytest.mark.anyio
async def test_turn_prompt_hook_blocks_on_explicit_decision() -> None:
    definitions = _definitions({
        "SessionStart": [_hook("start", matcher="startup")],
        "UserPromptSubmit": [_hook("prompt")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "additionalContext": "Python 3.13 is required.",
        },
        definitions[1].key: {
            "continue": False,
            "reason": "prompt blocked",
            "additionalContext": "Available projects: web, app, service.",
        },
    })
    events = TurnHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ), _invocation(session_started=True)))

    with pytest.raises(PromptHookBlockedError, match="prompt blocked") as caught:
        await events.begin("hello")

    assert caught.value.additional_context == (
        "Python 3.13 is required.",
        "Available projects: web, app, service.",
    )


@pytest.mark.anyio
async def test_stop_continue_false_takes_priority_over_continuation() -> None:
    definitions = _definitions({
        "Stop": [
            _hook("continue"),
            _hook("stop"),
        ],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "decision": "block",
            "reason": "run another pass",
        },
        definitions[1].key: {
            "continue": False,
            "stopReason": "finish now",
        },
    })
    events = TurnHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    )))

    decision = await events.stop(outcome="completed")

    assert not decision.should_continue
    assert len(runner.calls) == 2


@pytest.mark.anyio
async def test_tool_hooks_reject_invocation_from_another_scope() -> None:
    events = ToolHookEvents(_scope(HookRuntime.empty()))

    with pytest.raises(ValueError, match="does not belong to hook scope"):
        await events.pre_tool_use(_invocation(turn_id="other_turn"))


@pytest.mark.anyio
async def test_turn_runtime_keeps_pre_and_post_hooks_from_same_snapshot() -> None:
    old = _definitions({
        "PreToolUse": [_hook("old-pre")],
        "PostToolUse": [_hook("old-post")],
    })
    new = _definitions({
        "PreToolUse": [_hook("new-pre")],
        "PostToolUse": [_hook("new-post")],
    })
    runner = _CommandRunner()
    registry = HookRegistry(command_runner=runner)
    old_states = {
        definition.key: {"trusted_hash": definition.content_hash}
        for definition in old
    }
    coordinator = ToolCallCoordinator(_scope(registry.build(
        old,
        hook_states=old_states,
    )))
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation(_prepared):
        started.set()
        await release.wait()
        return _operation_result("done")

    task = asyncio.create_task(coordinator.run_invocation(
        _invocation(),
        operation,
    ))
    await started.wait()

    registry.build(
        new,
        hook_states={
            definition.key: {"trusted_hash": definition.content_hash}
            for definition in new
        },
    )
    release.set()
    result = await task

    assert result.value == "done"
    assert [call[0].handler.command for call in runner.calls] == [
        "old-pre",
        "old-post",
    ]


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
