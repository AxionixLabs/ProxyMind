# -*- coding: utf-8 -*-

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import mind_app.runtime.hooks.runtime as hook_runtime_module

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
from mind_app.runtime.hooks.output_spill import HookOutputSpillStore
from mind_app.runtime.hooks.registry import HookRegistry
from mind_app.runtime.hooks.runtime import HookRuntime
from mind_app.runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from mind_app.runtime.hooks.session import SessionLifecycleGateway
from mind_app.runtime.hooks.status import HookStatusCoordinator
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
from mind_core.hook_discovery import resolve_hook_definitions
from mind_core.hooks import HOOK_EVENT_NAMES
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
        return SimpleNamespace(data=_wire_output(
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


@pytest.mark.anyio
async def test_session_end_gateway_dispatches_once_and_cleans_spills() -> None:
    definitions = _definitions({
        "SessionEnd": [_hook("audit", matcher="other")],
    })
    runner = _CommandRunner()
    runtime = HookRuntime(definitions, command_runner=runner)
    context = _scope(runtime).context
    cleaned = []
    prepared = []

    async def cleanup_session(session_id):
        cleaned.append(session_id)

    gateway = SessionLifecycleGateway(
        scope_factory=lambda event_context: HookExecutionScope(
            context=event_context,
            dispatcher=runtime,
        ),
        cleanup_session=cleanup_session,
    )

    first = await gateway.end(
        7,
        context,
        reason="exit",
        transcript_path="D:/logs/transcript.log",
        last_assistant_message="final answer",
        before_dispatch=lambda: prepared.append(context.session_id),
    )
    second = await gateway.end(
        7,
        context,
        reason="exit",
        transcript_path="D:/logs/transcript.log",
        last_assistant_message="final answer",
        before_dispatch=lambda: prepared.append(context.session_id),
    )

    assert first is True
    assert second is False
    assert cleaned == ["sid_test"]
    assert prepared == ["sid_test"]
    assert len(runner.calls) == 1
    payload = runner.calls[0][1]
    assert payload["session_id"] == "sid_test"
    assert payload["hook_event_name"] == "SessionEnd"
    assert payload["reason"] == "other"
    assert payload["transcript_path"] == "D:/logs/transcript.log"
    assert "root_session_id" not in payload
    assert "last_assistant_message" not in payload


@pytest.mark.anyio
async def test_session_end_gateway_dispatches_after_preparation_failure() -> None:
    definitions = _definitions({
        "SessionEnd": [_hook("audit", matcher="other")],
    })
    runner = _CommandRunner()
    runtime = HookRuntime(definitions, command_runner=runner)
    context = _scope(runtime).context
    cleaned = []

    async def cleanup_session(session_id):
        cleaned.append(session_id)

    def fail_preparation() -> None:
        raise RuntimeError("transcript unavailable")

    gateway = SessionLifecycleGateway(
        scope_factory=lambda event_context: HookExecutionScope(
            context=event_context,
            dispatcher=runtime,
        ),
        cleanup_session=cleanup_session,
    )

    ended = await gateway.end(
        8,
        context,
        reason="exit",
        transcript_path="D:/sessions/session.jsonl",
        last_assistant_message="final answer",
        before_dispatch=fail_preparation,
    )

    assert ended is True
    assert cleaned == ["sid_test"]
    assert len(runner.calls) == 1


def test_runtime_reports_matching_hooks() -> None:
    definitions = _definitions({
        "PreToolUse": [
            _hook("shell", matcher="shell_command"),
            _hook("patch", matcher="apply_patch"),
        ],
    })
    runtime = HookRuntime(definitions)

    assert runtime.has_matching("PreToolUse", "shell_command")
    assert runtime.has_matching("PreToolUse", "apply_patch")
    assert not runtime.has_matching("PostToolUse", "shell_command")


def test_tool_matcher_uses_exact_names_and_alias_candidates() -> None:
    definitions = _definitions({
        "PreToolUse": [
            _hook("bash", matcher="Bash"),
            _hook("patch", matcher="Edit|Write"),
        ],
    })
    runtime = HookRuntime(definitions)

    assert runtime.has_matching("PreToolUse", "shell_command")
    assert not runtime.has_matching("PreToolUse", "BashOutput")
    assert runtime.has_matching("PreToolUse", "apply_patch")


def test_spawn_agent_matches_agent_alias() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("agent", matcher="Agent")],
    })

    assert HookRuntime(definitions).has_matching("PreToolUse", "spawn_agent")


@pytest.mark.anyio
async def test_tool_aliases_match_one_hook_once() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("patch", matcher="Edit|Write")],
    })
    runner = _CommandRunner()

    result = await HookRuntime(
        definitions,
        command_runner=runner,
    ).dispatch(HookEventRequest(
        event="PreToolUse",
        match_value="apply_patch",
        payload={},
    ))

    assert [record.hook_key for record in result.records] == [
        definitions[0].key,
    ]
    assert [call[0].key for call in runner.calls] == [definitions[0].key]


def test_matcher_group_expands_handlers_with_platform_commands() -> None:
    definitions = _definitions({
        "PreToolUse": [{
            "matcher": "shell_command",
            "hooks": [
                {
                    "type": "command",
                    "command": "check-posix",
                    "commandWindows": "check-windows",
                },
                {"type": "command", "command": "audit"},
            ],
        }],
    })

    assert len(definitions) == 2
    assert definitions[0].key.endswith(":PreToolUse:0:0")
    assert definitions[1].key.endswith(":PreToolUse:0:1")
    assert definitions[0].handler.command_for_platform("posix") == "check-posix"
    assert definitions[0].handler.command_for_platform("nt") == "check-windows"


def test_non_session_async_hook_is_skipped_with_warning() -> None:
    warnings = []
    definitions = resolve_hook_definitions(
        {
            "PreToolUse": [_hook(
                "background-check",
                run_async=True,
                status_message="Checking in background",
            )],
        },
        source_scope="user",
        source_path=Path("config.toml"),
        warnings=warnings,
    )

    assert definitions == ()
    assert len(warnings) == 1
    assert "skipping async hook" in warnings[0]


@pytest.mark.anyio
async def test_async_session_end_hook_runs_synchronously() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class Runner(object):
        async def execute(self, _definition, _payload):
            started.set()
            await release.wait()
            return SimpleNamespace(data={})

    definitions = _definitions({
        "SessionEnd": [_hook("close", run_async=True)],
    })
    task = asyncio.create_task(HookRuntime(
        definitions,
        command_runner=Runner(),
    ).dispatch(HookEventRequest(
        event="SessionEnd",
        match_value="other",
        payload={},
    )))

    await asyncio.wait_for(started.wait(), timeout=1)
    assert not task.done()
    release.set()
    result = await task

    assert len(result.records) == 1
    assert result.records[0].ok


@pytest.mark.anyio
async def test_additional_context_limit_spills_before_aggregation() -> None:
    class Spiller(object):
        def __init__(self) -> None:
            self.calls = []

        async def spill_context(self, text, *, session_id):
            self.calls.append((text, session_id))
            return "context spilled to D:/tmp/context.log"

    definitions = _definitions({
        "PreToolUse": [_hook(
            "context",
            additional_context_limit=1,
        )],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"additionalContext": "0123456789"},
    })
    spiller = Spiller()
    runtime = HookRuntime(
        definitions,
        command_runner=runner,
        context_spiller=spiller,
    )

    decision = await ToolHookEvents(_scope(runtime)).pre_tool_use(_invocation())

    assert decision.additional_context == (
        "context spilled to D:/tmp/context.log",
    )
    assert spiller.calls == [("0123456789", "sid_test")]


@pytest.mark.anyio
async def test_hook_status_coordinator_tracks_concurrent_messages() -> None:
    class FrontendRuntime(object):
        def __init__(self) -> None:
            self.started = []
            self.completed = []

        async def begin_operation_status(self, snapshot):
            self.started.append(snapshot)

        async def end_activity_status(self, kind, *, settle=True):
            self.completed.append((kind, settle))

    frontend = FrontendRuntime()
    status = HookStatusCoordinator(frontend)

    await status.started("first", "First check")
    await status.started("second", "Second check")

    assert len(frontend.started) == 1
    assert frontend.started[0]() == {"summary": "Second check"}

    await status.completed("first")
    assert frontend.completed == []

    await status.completed("second")
    assert frontend.completed == [("operation", False)]


@pytest.mark.anyio
async def test_context_spill_file_is_cleaned_with_session(tmp_path) -> None:
    store = HookOutputSpillStore(root=tmp_path / "spill")

    spill = await store.spill_text(
        "full additional context",
        session_id="sid",
        channel="additional-context",
    )
    path = Path(spill.path)

    assert path.read_text(encoding="utf-8") == "full additional context"

    await store.cleanup_session("sid")

    assert not path.exists()


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
async def test_runtime_treats_star_matcher_as_match_all() -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("check", matcher="*")],
    })
    runner = _CommandRunner()

    await HookRuntime(
        definitions,
        command_runner=runner,
    ).dispatch(HookEventRequest(
        event="PreToolUse",
        match_value="shell_command",
        payload={},
    ))

    assert [call[0].key for call in runner.calls] == [definitions[0].key]


@pytest.mark.anyio
@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop"])
async def test_runtime_ignores_matcher_for_events_without_match_subject(event) -> None:
    definitions = _definitions({
        event: [_hook("check", matcher="configured-but-ignored")],
    })
    runner = _CommandRunner()

    await HookRuntime(
        definitions,
        command_runner=runner,
    ).dispatch(HookEventRequest(
        event=event,
        match_value="",
        payload={},
    ))

    assert [call[0].key for call in runner.calls] == [definitions[0].key]


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
            return SimpleNamespace(data={
                "systemMessage": definition.handler.command,
            })

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

    assert [record.effect.warning for record in result.records] == [
        "first",
        "second",
    ]


@pytest.mark.anyio
async def test_runtime_records_system_message_as_warning(monkeypatch) -> None:
    definitions = _definitions({
        "SessionStart": [_hook("policy", matcher="startup")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {"systemMessage": "Policy check completed"},
    })
    observed = []
    monkeypatch.setattr(
        hook_runtime_module,
        "observe",
        lambda event, **fields: observed.append((event, fields)),
    )

    await HookRuntime(definitions, command_runner=runner).dispatch(
        HookEventRequest(
            event="SessionStart",
            match_value="startup",
            payload={"session_id": "sid_test"},
        )
    )

    assert observed == [(
        "hook.warning",
        {
            "level": "WARNING",
            "hook_key": definitions[0].key,
            "hook_event": "SessionStart",
            "message": "Policy check completed",
        },
    )]


@pytest.mark.anyio
async def test_pre_tool_use_uses_last_completed_updated_input() -> None:
    earlier_finished = asyncio.Event()

    class Runner(object):
        async def execute(self, definition, _payload):
            if definition.handler.command == "later-full":
                await earlier_finished.wait()
                output = {
                    "updatedInput": {"command": "A", "cwd": "/a"},
                }
            else:
                earlier_finished.set()
                output = {"updatedInput": {"command": "B"}}
            return SimpleNamespace(data=_wire_output(
                definition.event,
                output,
            ))

    definitions = _definitions({
        "PreToolUse": [
            _hook("later-full"),
            _hook("earlier-partial"),
        ],
    })
    decision = await ToolHookEvents(_scope(HookRuntime(
        definitions,
        command_runner=Runner(),
    ))).pre_tool_use(_invocation())

    assert decision.allowed
    assert decision.updated_input == {"command": "A", "cwd": "/a"}


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
        _invocation(execution={"grantId": "secret-grant"})
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

    assert denied.plain_text == "• Hook denied pytest -q"
    assert approved.plain_text == "✔ Hook approved pytest -q"


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
    assert result.visible_result.system_message == ""


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
    assert result.visible_result.system_message == ""


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
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', "
        "'permissionDecision': 'deny', 'permissionDecisionReason': "
        "payload['tool_name']}}))\n",
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
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "shell_command",
        },
    }


@pytest.mark.anyio
async def test_command_executor_uses_non_json_stdout_as_context(tmp_path) -> None:
    script = tmp_path / "invalid_hook.py"
    script.write_text("print('not-json')\n", encoding="utf-8")
    definition = _definitions({
        "PreToolUse": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]

    output = await HookCommandExecutor().execute(
        definition,
        {"cwd": str(tmp_path)},
    )

    assert output.data == {}


@pytest.mark.anyio
async def test_command_executor_rejects_json_looking_invalid_stdout(
    tmp_path,
) -> None:
    script = tmp_path / "invalid_json_hook.py"
    script.write_text("print('{invalid')\n", encoding="utf-8")
    definition = _definitions({
        "PreToolUse": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]

    with pytest.raises(HookCommandError, match="invalid JSON"):
        await HookCommandExecutor().execute(
            definition,
            {"cwd": str(tmp_path)},
        )


@pytest.mark.anyio
async def test_command_executor_exit_two_is_business_block(tmp_path) -> None:
    script = tmp_path / "blocking_hook.py"
    script.write_text(
        "import sys\n"
        "print('policy denied', file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    definition = _definitions({
        "PreToolUse": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]

    result = await HookRuntime(
        (definition,),
        command_runner=HookCommandExecutor(),
    ).dispatch(HookEventRequest(
        event="PreToolUse",
        payload={"cwd": str(tmp_path), "session_id": "sid"},
    ))

    assert result.records[0].ok
    assert result.records[0].stderr == "policy denied"
    assert not result.records[0].effect.continue_execution
    assert result.records[0].effect.reason == "policy denied"


@pytest.mark.anyio
async def test_command_executor_exit_two_requires_stderr_reason(tmp_path) -> None:
    script = tmp_path / "silent_blocking_hook.py"
    script.write_text("raise SystemExit(2)\n", encoding="utf-8")
    definition = _definitions({
        "PermissionRequest": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]

    with pytest.raises(HookCommandError, match="reason on stderr"):
        await HookCommandExecutor().execute(
            definition,
            {"cwd": str(tmp_path)},
        )


@pytest.mark.anyio
async def test_session_start_exit_two_is_a_failure(tmp_path) -> None:
    script = tmp_path / "failed_start_hook.py"
    script.write_text(
        "import sys\nprint('failed', file=sys.stderr)\nraise SystemExit(2)\n",
        encoding="utf-8",
    )
    definition = _definitions({
        "SessionStart": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]

    with pytest.raises(HookCommandError, match="code 2: failed"):
        await HookCommandExecutor().execute(
            definition,
            {"cwd": str(tmp_path)},
        )


@pytest.mark.anyio
async def test_command_executor_other_nonzero_exit_is_failure(tmp_path) -> None:
    script = tmp_path / "failed_hook.py"
    script.write_text(
        "import sys\n"
        "print('runtime failed', file=sys.stderr)\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    definition = _definitions({
        "PreToolUse": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]

    with pytest.raises(HookCommandError, match="code 3: runtime failed"):
        await HookCommandExecutor().execute(
            definition,
            {"cwd": str(tmp_path), "session_id": "sid"},
        )


@pytest.mark.anyio
async def test_command_executor_spills_large_stdout_by_session(tmp_path) -> None:
    script = tmp_path / "large_hook.py"
    script.write_text(
        "print('HEAD-' + ('x' * 160) + '-TAIL')\n",
        encoding="utf-8",
    )
    definition = _definitions({
        "SessionStart": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]
    executor = HookCommandExecutor(spill_store=HookOutputSpillStore(
        threshold_bytes=64,
        root=tmp_path / "spill",
    ))

    output = await executor.execute(
        definition,
        {"cwd": str(tmp_path), "session_id": "sid"},
    )

    spill = output.data["outputSpill"]["stdout"]
    spill_path = Path(spill["path"])
    assert spill_path.exists()
    assert spill["size_bytes"] > 64
    assert spill["head"].startswith("HEAD-")
    assert spill["tail"].endswith("-TAIL")
    assert str(spill_path) in output.data["stdout"]

    await executor.cleanup_session("sid")

    assert not spill_path.exists()


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
            timeout=1,
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
