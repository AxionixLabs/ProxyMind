# -*- coding: utf-8 -*-

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mind_app.runtime.execution import (
    AgentContext,
    ToolInvocation,
    TurnContext
)
from mind_app.runtime.hooks.command import (
    HookCommandError,
    HookCommandExecutor
)
from mind_app.runtime.hooks.events import HOOK_EVENT_SPECS
from mind_app.runtime.hooks.models import HookEventRequest
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.tool import (
    ToolCallCoordinator,
    ToolHookEvents
)
from mind_core.hooks import (
    HOOK_EVENT_NAMES,
    resolve_hook_definitions
)
from mind_core.permissions import preset_permissions


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
        return SimpleNamespace(data=dict(self.outputs.get(definition.key) or {}))


def _definitions(raw):
    return resolve_hook_definitions(
        raw,
        source_scope="user",
        source_path=Path("hooks.toml"),
    )


def _invocation(*, execution=None) -> ToolInvocation:
    turn = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        mode="xtra",
        source="test",
        pref_config={"primary": {"model": "test-model"}},
        cwd=".",
        permissions=preset_permissions("auto"),
        turn_id="turn_test",
    )
    return ToolInvocation(
        turn=turn,
        call_id="call_test",
        name="shell_command",
        arguments={"command": "rg TODO"},
        meta={"domain": "coding"},
        execution=execution,
    )


def test_runtime_event_specs_cover_config_event_catalog() -> None:
    assert tuple(HOOK_EVENT_SPECS) == HOOK_EVENT_NAMES


def test_runtime_reports_matching_active_hooks() -> None:
    definitions = _definitions({
        "PreToolUse": [
            {"command": "enabled", "matcher": "shell_command"},
            {
                "command": "disabled",
                "matcher": "apply_patch",
                "enabled": False,
            },
        ],
    })
    runtime = HookRuntime(definitions)

    assert runtime.has_matching("PreToolUse", "shell_command")
    assert not runtime.has_matching("PreToolUse", "apply_patch")
    assert not runtime.has_matching("PostToolUse", "shell_command")


@pytest.mark.anyio
async def test_runtime_dispatches_only_matching_hooks_in_definition_order() -> None:
    definitions = _definitions({
        "PreToolUse": [
            {"command": "first", "matcher": "shell_.*"},
            {"command": "other", "matcher": "apply_patch"},
            {"command": "second", "matcher": "shell_command"},
        ],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"decision": "allow"},
        definitions[2].key: {"continue": True},
    })

    result = await HookRuntime(
        definitions,
        command_runner=runner,
    ).dispatch(HookEventRequest(
        event="PreToolUse",
        match_value="shell_command",
        payload={"cwd": ".", "hook_event_name": "invalid"},
    ))

    assert [record.hook_key for record in result.records] == [
        definitions[0].key,
        definitions[2].key,
    ]
    assert all(record.ok for record in result.records)
    assert [call[1]["hook_event_name"] for call in runner.calls] == [
        "PreToolUse",
        "PreToolUse",
    ]


@pytest.mark.anyio
async def test_turn_runtime_keeps_pre_and_post_hooks_from_same_snapshot() -> None:
    old = _definitions({
        "PreToolUse": [{"command": "old-pre"}],
        "PostToolUse": [{"command": "old-post"}],
    })
    new = _definitions({
        "PreToolUse": [{"command": "new-pre"}],
        "PostToolUse": [{"command": "new-post"}],
    })
    runner = _CommandRunner()
    registry = HookRegistry(command_runner=runner)
    coordinator = ToolCallCoordinator(registry.build(old))
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        return "done"

    task = asyncio.create_task(coordinator.run(_invocation(), operation))
    await started.wait()

    registry.build(new)
    release.set()
    result = await task

    assert result.value == "done"
    assert [call[0].command for call in runner.calls] == [
        "old-pre",
        "old-post",
    ]


@pytest.mark.anyio
async def test_pre_tool_use_aggregates_deny_and_omits_execution_metadata() -> None:
    definitions = _definitions({
        "PreToolUse": [
            {"command": "allow", "matcher": "shell_.*"},
            {"command": "deny", "matcher": "shell_command"},
        ],
    })
    runner = _CommandRunner(outputs={
        definitions[1].key: {
            "decision": "deny",
            "reason": "command is blocked",
        },
    })
    runtime = HookRuntime(definitions, command_runner=runner)

    decision = await ToolHookEvents(runtime).pre_tool_use(
        _invocation(execution={"grantId": "secret-grant"})
    )

    assert not decision.allowed
    assert decision.reason == "command is blocked"
    assert len(runner.calls) == 2
    payload = runner.calls[0][1]
    assert payload["agent_id"] == "root"
    assert payload["turn_id"] == "turn_test"
    assert payload["tool_input"] == {"command": "rg TODO"}
    assert "execution" not in payload
    assert "secret-grant" not in str(payload)


@pytest.mark.anyio
async def test_pre_tool_use_blocks_when_blocking_hook_fails() -> None:
    definitions = _definitions({
        "PreToolUse": [{
            "command": "broken",
            "on_error": "block",
        }],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("broken hook"),
    })

    decision = await ToolHookEvents(HookRuntime(
        definitions,
        command_runner=runner,
    )).pre_tool_use(_invocation())

    assert not decision.allowed
    assert "broken hook" in decision.reason


@pytest.mark.anyio
async def test_pre_tool_use_continues_when_nonblocking_hook_fails() -> None:
    definitions = _definitions({
        "PreToolUse": [{
            "command": "broken",
            "on_error": "continue",
        }],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("broken hook"),
    })

    decision = await ToolHookEvents(HookRuntime(
        definitions,
        command_runner=runner,
    )).pre_tool_use(_invocation())

    assert decision.allowed


@pytest.mark.anyio
async def test_pre_tool_use_treats_invalid_decision_as_hook_failure() -> None:
    definitions = _definitions({
        "PreToolUse": [{"command": "invalid"}],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"decision": "unknown"},
    })

    decision = await ToolHookEvents(HookRuntime(
        definitions,
        command_runner=runner,
    )).pre_tool_use(_invocation())

    assert not decision.allowed
    assert "decision must be allow, deny, or block" in decision.reason


@pytest.mark.anyio
async def test_tool_coordinator_reuses_prepared_decision_and_runs_post() -> None:
    definitions = _definitions({
        "PreToolUse": [{"command": "pre"}],
        "PostToolUse": [{"command": "post"}],
    })
    runner = _CommandRunner()
    coordinator = ToolCallCoordinator(
        HookRuntime(definitions, command_runner=runner)
    )
    invocation = _invocation()

    decision = await coordinator.prepare(invocation)
    result = await coordinator.run(
        invocation,
        lambda: _return_value(SimpleNamespace(
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
    assert runner.calls[1][1]["tool_outcome"]["ok"] is True
    assert runner.calls[1][1]["tool_outcome"]["result"]["data"] == {
        "answer": 42,
    }


@pytest.mark.anyio
async def test_post_tool_use_failure_does_not_replace_tool_result() -> None:
    definitions = _definitions({
        "PostToolUse": [{"command": "broken-post"}],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("audit failed"),
    })
    coordinator = ToolCallCoordinator(
        HookRuntime(definitions, command_runner=runner)
    )

    result = await coordinator.run(
        _invocation(),
        lambda: _return_value("done"),
    )

    assert result.allowed
    assert result.value == "done"


@pytest.mark.anyio
async def test_command_executor_uses_json_stdin_and_stdout(tmp_path) -> None:
    script = tmp_path / "hook.py"
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'decision': 'deny', 'reason': payload['tool_name']}))\n",
        encoding="utf-8",
    )
    command = subprocess.list2cmdline([sys.executable, str(script)])
    definition = _definitions({
        "PreToolUse": [{"command": command}],
    })[0]

    output = await HookCommandExecutor().execute(
        definition,
        {"cwd": str(tmp_path), "tool_name": "shell_command"},
    )

    assert output.data == {
        "decision": "deny",
        "reason": "shell_command",
    }


@pytest.mark.anyio
async def test_command_executor_rejects_invalid_json(tmp_path) -> None:
    script = tmp_path / "invalid_hook.py"
    script.write_text("print('not-json')\n", encoding="utf-8")
    definition = _definitions({
        "PreToolUse": [{
            "command": subprocess.list2cmdline([sys.executable, str(script)]),
        }],
    })[0]

    with pytest.raises(HookCommandError, match="not valid JSON"):
        await HookCommandExecutor().execute(
            definition,
            {"cwd": str(tmp_path)},
        )


async def _return_value(value):
    return value
