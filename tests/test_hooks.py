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
from mind_app.runtime.hooks.models import (
    HookEventRequest,
    ToolOperationResult,
    ToolResultSnapshot
)
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from mind_app.runtime.hooks.tool import (
    ToolCallCoordinator,
    ToolHookEvents
)
from mind_app.runtime.hooks.turn import (
    PromptHookBlockedError,
    TurnHookEvents
)
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.renderers.approval import render_approval_view
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


def _hook(
    command,
    *,
    matcher=None,
    on_error=None,
    enabled=None,
    timeout=None,
):
    handler = {"type": "command", "command": command}
    if timeout is not None:
        handler["timeout"] = timeout
    config = {"handler": handler}
    if matcher is not None:
        config["matcher"] = matcher
    if on_error is not None:
        config["on_error"] = on_error
    if enabled is not None:
        config["enabled"] = enabled
    return config


def _invocation(
    *,
    execution=None,
    turn_id: str = "turn_test",
    session_started: bool = False,
) -> ToolInvocation:
    turn = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        mode="xtra",
        source="test",
        pref_config={"primary": {"model": "test-model"}},
        cwd=".",
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
        execution=execution,
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


def test_runtime_event_specs_cover_config_event_catalog() -> None:
    assert tuple(HOOK_EVENT_SPECS) == HOOK_EVENT_NAMES


def test_runtime_reports_matching_active_hooks() -> None:
    definitions = _definitions({
        "PreToolUse": [
            _hook("enabled", matcher="shell_command"),
            _hook("disabled", matcher="apply_patch", enabled=False),
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
            _hook("first", matcher="shell_.*"),
            _hook("other", matcher="apply_patch"),
            _hook("second", matcher="shell_command"),
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
async def test_runtime_launches_matching_hooks_concurrently() -> None:
    definitions = _definitions({
        "PostToolUse": [
            _hook("first"),
            _hook("second"),
        ],
    })

    class ConcurrentRunner:
        def __init__(self) -> None:
            self.started = 0
            self.all_started = asyncio.Event()
            self.release = asyncio.Event()

        async def execute(self, definition, payload):
            self.started += 1
            if self.started == len(definitions):
                self.all_started.set()
            await self.release.wait()
            return SimpleNamespace(data={"reason": definition.handler.command})

    runner = ConcurrentRunner()
    dispatch = asyncio.create_task(HookRuntime(
        definitions,
        command_runner=runner,
    ).dispatch(HookEventRequest(
        event="PostToolUse",
        match_value="shell_command",
        payload={"cwd": "."},
    )))

    await asyncio.wait_for(runner.all_started.wait(), timeout=1)
    assert not dispatch.done()

    runner.release.set()
    result = await dispatch

    assert [record.output["reason"] for record in result.records] == [
        "first",
        "second",
    ]


@pytest.mark.anyio
async def test_runtime_cancellation_stops_all_matching_hooks() -> None:
    definitions = _definitions({
        "PostToolUse": [
            _hook("first"),
            _hook("second"),
        ],
    })

    class BlockingRunner:
        def __init__(self) -> None:
            self.started = 0
            self.all_started = asyncio.Event()
            self.cancelled = []

        async def execute(self, definition, payload):
            self.started += 1
            if self.started == len(definitions):
                self.all_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.append(definition.key)
                raise

    runner = BlockingRunner()
    dispatch = asyncio.create_task(HookRuntime(
        definitions,
        command_runner=runner,
    ).dispatch(HookEventRequest(
        event="PostToolUse",
        match_value="shell_command",
        payload={"cwd": "."},
    )))

    await asyncio.wait_for(runner.all_started.wait(), timeout=1)
    dispatch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await dispatch

    assert set(runner.cancelled) == {
        definitions[0].key,
        definitions[1].key,
    }


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
            "call_id": "call_test",
            "tool_name": "shell_command",
            "tool_kind": "coding",
            "tool_input": {"command": "rg TODO"},
        },
        match_value="shell_command",
    )

    payload = runner.calls[0][1]
    assert payload["session_id"] == "sid_test"
    assert payload["root_session_id"] == "sid_test"
    assert payload["conversation_id"] == "cid_test"
    assert payload["turn_id"] == "turn_test"
    assert payload["agent_id"] == "root"
    assert payload["agent_type"] == "root"
    assert payload["agent_depth"] == 0
    assert payload["mode"] == "xtra"
    assert payload["source"] == "test"


@pytest.mark.anyio
async def test_turn_hooks_dispatch_start_prompt_and_stop_in_order() -> None:
    definitions = _definitions({
        "SessionStart": [_hook("start", matcher="initial")],
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
    assert runner.calls[0][1]["reason"] == "initial"
    assert runner.calls[1][1]["prompt"] == "hello"
    assert runner.calls[2][1]["outcome"] == "completed"
    assert runner.calls[2][1]["usage"] == {"output_tokens": 3}


@pytest.mark.anyio
async def test_turn_hooks_skip_session_start_for_existing_session() -> None:
    definitions = _definitions({
        "SessionStart": [_hook("start", matcher="initial")],
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
@pytest.mark.parametrize(
    ("on_error", "blocked"),
    [("block", True), ("continue", False)],
)
async def test_turn_prompt_hook_applies_failure_policy(
    on_error,
    blocked,
) -> None:
    definitions = _definitions({
        "UserPromptSubmit": [_hook("broken", on_error=on_error)],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("prompt hook failed"),
    })
    events = TurnHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    )))

    if blocked:
        with pytest.raises(PromptHookBlockedError, match="prompt hook failed"):
            await events.begin("hello")
    else:
        await events.begin("hello")


@pytest.mark.anyio
async def test_turn_prompt_hook_blocks_on_explicit_decision() -> None:
    definitions = _definitions({
        "UserPromptSubmit": [_hook("prompt")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "continue": False,
            "reason": "prompt blocked",
        },
    })
    events = TurnHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    )))

    with pytest.raises(PromptHookBlockedError, match="prompt blocked"):
        await events.begin("hello")


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
    coordinator = ToolCallCoordinator(_scope(registry.build(old)))
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

    registry.build(new)
    release.set()
    result = await task

    assert result.value == "done"
    assert [call[0].handler.command for call in runner.calls] == [
        "old-pre",
        "old-post",
    ]


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
        },
    })
    runtime = HookRuntime(definitions, command_runner=runner)

    decision = await ToolHookEvents(_scope(runtime)).pre_tool_use(
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
        "PreToolUse": [_hook("broken", on_error="block")],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("broken hook"),
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).pre_tool_use(_invocation())

    assert not decision.allowed
    assert "broken hook" in decision.reason


@pytest.mark.anyio
async def test_pre_tool_use_continues_when_nonblocking_hook_fails() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("broken", on_error="continue")],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("broken hook"),
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).pre_tool_use(_invocation())

    assert decision.allowed


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

    assert not decision.allowed
    assert "decision must be one of allow, deny, block" in decision.reason


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
    ))).permission_request(_invocation(execution={"grantId": "secret"}))

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
async def test_permission_request_blocks_on_configured_hook_failure() -> None:
    definitions = _definitions({
        "PermissionRequest": [_hook("broken", on_error="block")],
    })
    runner = _CommandRunner(errors={
        definitions[0].key: RuntimeError("permission check failed"),
    })

    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=runner,
    ))).permission_request(_invocation())

    assert decision.action == "deny"
    assert "permission check failed" in decision.reason
    assert decision.hook_keys == (definitions[0].key,)


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

    assert denied.plain_text == "• Hook denied pytest -q"
    assert approved.plain_text == "✔ Hook approved pytest -q"


@pytest.mark.anyio
async def test_tool_coordinator_reuses_prepared_decision_and_runs_post() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("pre")],
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner()
    coordinator = ToolCallCoordinator(
        _scope(HookRuntime(definitions, command_runner=runner))
    )
    invocation = _invocation()

    decision = await coordinator.prepare(invocation)
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
    assert runner.calls[1][1]["tool_outcome"]["ok"] is True
    assert runner.calls[1][1]["tool_outcome"]["result"]["data"] == {
        "answer": 42,
    }


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
    assert result.visible_result.system_message == (
        "Treat the tool result as summarized.\n\nPost message."
    )


@pytest.mark.anyio
async def test_post_tool_use_exposes_replacement_result_effect() -> None:
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

    assert result.visible_result.text == "redacted"
    assert result.visible_result.fields["data"] == {"redacted": True}
    assert result.visible_result.additional_context == ("explain the redaction",)
    assert result.visible_result.system_message == "Do not reveal the original output."


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
        "PreToolUse": [_hook(command)],
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
        "PreToolUse": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]

    with pytest.raises(HookCommandError, match="not valid JSON"):
        await HookCommandExecutor().execute(
            definition,
            {"cwd": str(tmp_path)},
        )


@pytest.mark.anyio
async def test_command_executor_terminates_timed_out_hook(tmp_path) -> None:
    script = tmp_path / "slow_hook.py"
    script.write_text(
        "import time\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    definition = _definitions({
        "PostToolUse": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
            timeout=0.05,
        )],
    })[0]

    started_at = asyncio.get_running_loop().time()
    with pytest.raises(HookCommandError, match="timed out"):
        await HookCommandExecutor().execute(
            definition,
            {"cwd": str(tmp_path)},
        )
    assert asyncio.get_running_loop().time() - started_at < 2


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
