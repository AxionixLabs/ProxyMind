# -*- coding: utf-8 -*-

"""验证 Hook 驱动的工具权限裁决与失败策略。"""


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
async def test_pre_tool_use_aggregates_deny_and_omits_execution_metadata() -> None:
    definitions = _definitions({
        "PreToolUse": [
            _hook("allow", matcher="shell_.*"),
            _hook("deny", matcher="shell_command"),
        ],
    })
    runner = _CommandRunner(outputs={
        definitions[1].key: {
            "decision": "deny",
            "reason": "command is blocked",
            "additionalContext": "Use scripts/clean.py instead.",
        },
    })
    runtime = HookRuntime(definitions, command_runner=runner)

    decision = await ToolHookEvents(_scope(runtime)).pre_tool_use(
        _invocation()
    )

    assert not decision.allowed
    assert decision.reason == "command is blocked"
    assert decision.additional_context == ("Use scripts/clean.py instead.",)
    assert len(runner.calls) == 2
    payload = runner.calls[0][1]
    assert payload["turn_id"] == "turn_test"
    assert payload["tool_name"] == "Bash"
    assert payload["tool_use_id"] == "call_test"
    assert payload["tool_input"] == {"command": "rg TODO"}
    assert "execution" not in payload
    assert "secret-grant" not in str(payload)


@pytest.mark.anyio
async def test_pre_tool_use_uses_first_configured_deny_reason() -> None:
    definitions = _definitions({
        "PreToolUse": [
            _hook("first", matcher="shell_command"),
            _hook("second", matcher="shell_command"),
        ],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "decision": "deny",
            "reason": "first reason",
        },
        definitions[1].key: {
            "decision": "deny",
            "reason": "second reason",
        },
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).pre_tool_use(_invocation())

    assert not decision.allowed
    assert decision.reason == "first reason"
    assert decision.hook_keys == tuple(
        definition.key for definition in definitions
    )


@pytest.mark.anyio
async def test_pre_tool_use_failure_does_not_block() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("broken")],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("broken hook"),
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).pre_tool_use(_invocation())

    assert decision.allowed
    assert decision.reason == ""


@pytest.mark.anyio
async def test_pre_tool_use_treats_invalid_decision_as_hook_failure() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("invalid")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"decision": "unknown"},
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).pre_tool_use(_invocation())

    assert decision.allowed
    assert decision.reason == ""


@pytest.mark.anyio
async def test_permission_request_prioritizes_deny_over_allow() -> None:
    definitions = _definitions({
        "PermissionRequest": [
            _hook("allow", matcher="shell_.*"),
            _hook("abstain", matcher="shell_command"),
            _hook("deny", matcher="shell_command"),
        ],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"decision": "allow"},
        definitions[1].key: {},
        definitions[2].key: {
            "decision": "deny",
            "reason": "permission blocked",
        },
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).permission_request(_invocation())

    assert decision.action == "deny"
    assert decision.reason == "permission blocked"
    assert decision.hook_keys == (definitions[2].key,)
    assert len(runner.calls) == 3
    payload = runner.calls[0][1]
    assert payload["hook_event_name"] == "PermissionRequest"
    assert payload["tool_input"] == {"command": "rg TODO"}
    assert "execution" not in payload
    assert "secret" not in str(payload)


@pytest.mark.anyio
async def test_permission_request_abstains_without_matching_hook() -> None:
    decision = await ToolHookEvents(_scope(HookRuntime.empty())).permission_request(
        _invocation()
    )

    assert decision.action == "abstain"
    assert decision.hook_keys == ()


@pytest.mark.anyio
async def test_permission_request_allows_when_a_hook_allows() -> None:
    definitions = _definitions({
        "PermissionRequest": [_hook("allow")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"decision": "allow"},
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).permission_request(_invocation())

    assert decision.action == "allow"
    assert decision.hook_keys == (definitions[0].key,)


@pytest.mark.anyio
async def test_permission_request_uses_event_failure_policy() -> None:
    definitions = _definitions({
        "PermissionRequest": [_hook("broken")],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("permission check failed"),
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).permission_request(_invocation())

    assert decision.action == "abstain"
    assert decision.reason == ""
    assert decision.hook_keys == ()


@pytest.mark.anyio
async def test_permission_preparation_stops_after_pre_tool_denial() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("deny-pre")],
        "PermissionRequest": [_hook("allow-permission")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "decision": "deny",
            "reason": "blocked before approval",
            "additionalContext": "Use the safe wrapper instead.",
        },
        definitions[1].key: {"decision": "allow"},
    })
    coordinator = ToolCallCoordinator(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    )))

    decision = await coordinator.prepare_permission(_invocation())

    assert decision.action == "deny"
    assert decision.reason == "blocked before approval"
    assert decision.hook_keys == (definitions[0].key,)
    assert decision.additional_context == ("Use the safe wrapper instead.",)
    assert [call[0].event for call in runner.calls] == ["PreToolUse"]


def test_hook_driven_approval_has_neutral_actor_text() -> None:
    approval = {
        "tool": "shell_command",
        "arguments": {"command": "pytest -q"},
    }

    denied = render_approval_view(build_approval_view(
        approval,
        decision="decline",
        source="hook",
    ))
    approved = render_approval_view(build_approval_view(
        approval,
        decision="accept",
        source="hook",
    ))

    assert denied.plain_text == "✗ Hook denied pytest -q"
    assert approved.plain_text == "✔ Hook approved pytest -q"
