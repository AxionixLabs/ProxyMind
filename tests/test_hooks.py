# -*- coding: utf-8 -*-

import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import mind_app.runtime.hooks.command as hook_command_module
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
    HookOutputEntry,
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
from mind_app.runtime.hooks.tool import (
    CommandHookSessionStore,
    ToolCallCoordinator,
    ToolHookEvents
)
from mind_app.runtime.hooks.turn import (
    PromptHookBlockedError,
    TurnHookEvents
)
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.renderers.approval import render_approval_view
from infrastructure.hooks.discovery import resolve_hook_definitions
from agent.application import HOOK_EVENT_NAMES
from agent.application import preset_permissions


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


class _RecordingHookStatus:
    def __init__(self) -> None:
        self.started_runs = []
        self.completed_runs = []

    async def started(self, run):
        self.started_runs.append(run)

    async def completed(self, run):
        self.completed_runs.append(run)


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


@pytest.mark.parametrize(
    ("matcher", "tool_name"),
    [
        ("shell_command", "Bash"),
        ("apply_patch", "Write"),
        ("apply_patch", "Edit"),
        ("spawn_agent", "Agent"),
    ],
)
def test_tool_aliases_do_not_reverse_match_external_names(
    matcher: str,
    tool_name: str,
) -> None:
    definitions = _definitions({
        "PreToolUse": [_hook("builtin", matcher=matcher)],
    })

    assert not HookRuntime(definitions).has_matching("PreToolUse", tool_name)


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


def test_discovery_supports_all_codex_handler_types_and_stable_selectors() -> None:
    raw = {
        "PreToolUse": [{
            "matcher": "shell_command",
            "hooks": [
                {"type": "command", "command": "check"},
                {"type": "mcp_tool", "server": "files", "tool": "read"},
                {"type": "prompt"},
                {"type": "agent"},
            ],
        }],
    }
    definitions = resolve_hook_definitions(
        raw,
        source_scope="project",
        source_path=None,
    )

    assert [item.handler.type for item in definitions] == [
        "command", "mcp_tool", "prompt", "agent",
    ]
    assert "|mcp_tool:files:read:" in definitions[1].key
    assert "|prompt:" in definitions[2].key
    assert definitions[1].handler.command is None
    assert definitions[1].handler.mcp_server == "files"
    assert definitions[1].handler.mcp_tool == "read"


def test_handler_content_changes_update_content_hash() -> None:
    first = resolve_hook_definitions(
        {"PreToolUse": [{"hooks": [{"type": "prompt"}]}]},
        source_scope="project",
        source_path=None,
    )[0]
    second = resolve_hook_definitions(
        {"PreToolUse": [{"hooks": [{"type": "agent"}]}]},
        source_scope="project",
        source_path=None,
    )[0]
    assert first.content_hash != second.content_hash


def test_registry_keeps_non_command_hooks_in_catalog_but_out_of_runtime() -> None:
    definitions = resolve_hook_definitions(
        {"PreToolUse": [{"hooks": [
            {"type": "mcp_tool", "server": "files", "tool": "read"},
            {"type": "command", "command": "check"},
        ]}]},
        source_scope="project",
        source_path=None,
    )
    registry = HookRegistry(command_runner=_CommandRunner())
    runtime = registry.build(
        definitions,
        hook_states={
            item.key: {"trusted_hash": item.content_hash}
            for item in definitions
        },
    )
    assert [item.handler.type for item in runtime.definitions] == ["command"]
    assert runtime.installed_count == 2
    assert runtime.active_count == 1
    assert runtime.status().warnings == (
        "skipping MCP tool hook in hooks configuration: "
        "MCP invocation is not available yet",
    )
    snapshot = registry.inspect(
        definitions,
        workspace=Path("."),
    )
    assert [item.handler_type for item in snapshot.hooks] == ["mcp_tool", "command"]


def test_registry_reports_each_unsupported_mcp_hook_source_once(tmp_path) -> None:
    first_path = tmp_path / "first.toml"
    second_path = tmp_path / "second.toml"
    definitions = (
        *resolve_hook_definitions(
            {"PreToolUse": [{"hooks": [
                {"type": "mcp_tool", "server": "files", "tool": "read"},
                {"type": "mcp_tool", "server": "files", "tool": "write"},
            ]}]},
            source_scope="user",
            source_path=first_path,
        ),
        *resolve_hook_definitions(
            {"PostToolUse": [{"hooks": [
                {"type": "mcp_tool", "server": "files", "tool": "audit"},
            ]}]},
            source_scope="project",
            source_path=second_path,
        ),
    )
    states = {
        item.key: {"trusted_hash": item.content_hash}
        for item in definitions
    }

    warnings = HookRegistry().startup_warnings(
        definitions,
        hook_states=states,
    )

    assert warnings == (
        f"skipping MCP tool hook in {first_path}: "
        "MCP invocation is not available yet",
        f"skipping MCP tool hook in {second_path}: "
        "MCP invocation is not available yet",
    )


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

        async def spill_context(
            self,
            text,
            *,
            session_id,
            channel="additional-context",
            preview_chars=None,
        ):
            self.calls.append((text, session_id, channel, preview_chars))
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
    assert spiller.calls == [(
        "0123456789",
        "sid_test",
        "additional-context",
        4,
    )]


@pytest.mark.anyio
async def test_stop_continuation_limit_spills_before_aggregation() -> None:
    class Spiller(object):
        def __init__(self) -> None:
            self.calls = []

        async def spill_context(
            self,
            text,
            *,
            session_id,
            channel="additional-context",
            preview_chars=None,
        ):
            self.calls.append((text, session_id, channel, preview_chars))
            return "continuation spilled to D:/tmp/continuation.log"

    definitions = _definitions({
        "Stop": [_hook(
            "continue",
            additional_context_limit=1,
        )],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "decision": "block",
            "reason": "0123456789",
        },
    })
    spiller = Spiller()
    runtime = HookRuntime(
        definitions,
        command_runner=runner,
        context_spiller=spiller,
    )

    result = await runtime.dispatch(HookEventRequest(
        event="Stop",
        payload={"session_id": "sid_test"},
    ))

    record = result.records[0]
    summary = "continuation spilled to D:/tmp/continuation.log"
    assert record.output["reason"] == summary
    assert record.effect.reason == summary
    assert record.effect.continuation_prompt == summary
    assert spiller.calls == [(
        "0123456789",
        "sid_test",
        "continuation-prompt",
        4,
    )]


@pytest.mark.anyio
async def test_runtime_publishes_successful_hook_lifecycle() -> None:
    definitions = _definitions({
        "SessionStart": [_hook("check")],
    })
    status = _RecordingHookStatus()

    result = await HookRuntime(
        definitions,
        command_runner=_CommandRunner(),
        status_port=status,
    ).dispatch(HookEventRequest(
        event="SessionStart",
        match_value="startup",
        payload={},
    ))

    assert result.records[0].ok
    assert len(status.started_runs) == 1
    assert len(status.completed_runs) == 1

    started = status.started_runs[0]
    completed = status.completed_runs[0]
    assert started.status == "running"
    assert started.status_message == ""
    assert started.completed_at is None
    assert started.duration_ms is None
    assert completed.id == started.id
    assert completed.hook_key == definitions[0].key
    assert completed.event == "SessionStart"
    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.completed_at >= completed.started_at
    assert completed.duration_ms is not None
    assert completed.duration_ms >= 0
    assert completed.entries == ()


@pytest.mark.anyio
async def test_runtime_assigns_unique_ids_to_concurrent_hook_invocations() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("audit", status_message="Auditing")],
    })

    class ConcurrentRunner:
        def __init__(self) -> None:
            self.count = 0
            self.all_started = asyncio.Event()
            self.release = asyncio.Event()

        async def execute(self, _definition, _payload):
            self.count += 1
            if self.count == 2:
                self.all_started.set()
            await self.release.wait()
            return SimpleNamespace(data={})

    runner = ConcurrentRunner()
    status = _RecordingHookStatus()
    runtime = HookRuntime(
        definitions,
        command_runner=runner,
        status_port=status,
    )
    request = HookEventRequest(
        event="PostToolUse",
        match_value="shell_command",
        payload={},
    )
    first = asyncio.create_task(runtime.dispatch(request))
    second = asyncio.create_task(runtime.dispatch(request))

    await asyncio.wait_for(runner.all_started.wait(), timeout=1)
    assert len(status.started_runs) == 2
    assert len({run.id for run in status.started_runs}) == 2
    assert {run.hook_key for run in status.started_runs} == {
        definitions[0].key,
    }

    runner.release.set()
    await asyncio.gather(first, second)

    assert {run.id for run in status.completed_runs} == {
        run.id for run in status.started_runs
    }
    assert all(run.status == "completed" for run in status.completed_runs)


@pytest.mark.anyio
async def test_runtime_publishes_failed_and_blocked_hook_results() -> None:
    failed_definition = _definitions({
        "SessionStart": [_hook("fail")],
    })[0]
    failed_status = _RecordingHookStatus()

    await HookRuntime(
        (failed_definition,),
        command_runner=_CommandRunner(errors={
            failed_definition.key: RuntimeError("hook failed"),
        }),
        status_port=failed_status,
    ).dispatch(HookEventRequest(
        event="SessionStart",
        match_value="startup",
        payload={},
    ))

    failed = failed_status.completed_runs[0]
    assert failed.status == "failed"
    assert failed.entries == (HookOutputEntry("error", "hook failed"),)

    blocked_definition = _definitions({
        "PreToolUse": [_hook("deny")],
    })[0]
    blocked_status = _RecordingHookStatus()

    await HookRuntime(
        (blocked_definition,),
        command_runner=_CommandRunner(outputs={
            blocked_definition.key: {
                "decision": "deny",
                "reason": "protected path",
            },
        }),
        status_port=blocked_status,
    ).dispatch(HookEventRequest(
        event="PreToolUse",
        match_value="shell_command",
        payload={},
    ))

    blocked = blocked_status.completed_runs[0]
    assert blocked.status == "blocked"
    assert blocked.entries == (
        HookOutputEntry("feedback", "protected path"),
    )


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
    status = _RecordingHookStatus()
    dispatch = asyncio.create_task(HookRuntime(
        definitions,
        command_runner=runner,
        status_port=status,
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
    assert {run.id for run in status.completed_runs} == {
        run.id for run in status.started_runs
    }
    assert all(run.status == "stopped" for run in status.completed_runs)


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
async def test_post_tool_use_block_rejects_result_before_replacement() -> None:
    definitions = _definitions({
        "PostToolUse": [_hook("post")],
    })
    runner = _CommandRunner(outputs={
        definitions[0].key: {
            "decision": "block",
            "reason": "reject this result",
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
    assert result.visible_result.ok is False
    assert result.visible_result.text == "reject this result"
    assert result.visible_result.fields == {
        "ok": False,
        "text": "reject this result",
        "attachments": [],
        "data": {
            "hook_blocked": True,
            "error": "reject this result",
        },
    }


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
@pytest.mark.skipif(os.name != "nt", reason="Windows command quoting")
async def test_command_executor_runs_windows_command_from_spaced_path(
    tmp_path,
) -> None:
    script_dir = tmp_path / "hook scripts"
    script_dir.mkdir()
    script = script_dir / "echo hook.py"
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'value': payload['value']}))\n",
        encoding="utf-8",
    )
    command = subprocess.list2cmdline([sys.executable, str(script)])
    definition = _definitions({
        "PreToolUse": [_hook("unused", command_windows=command)],
    })[0]

    output = await HookCommandExecutor().execute(
        definition,
        {"cwd": str(tmp_path), "value": "ok"},
    )

    assert output.data == {"value": "ok"}


@pytest.mark.anyio
async def test_command_executor_uses_login_shell_on_posix(
    monkeypatch,
) -> None:
    calls = []
    process = object()

    async def create_process(*args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(hook_command_module.os, "name", "posix")
    monkeypatch.setenv("SHELL", "/bin/example-shell")
    monkeypatch.setattr(
        hook_command_module.asyncio,
        "create_subprocess_exec",
        create_process,
    )

    started = await HookCommandExecutor._start_process(
        "source env.sh && check-hook",
        cwd="/tmp/workspace",
    )

    assert started is process
    assert calls[0][0] == (
        "/bin/example-shell",
        "-lc",
        "source env.sh && check-hook",
    )
    assert calls[0][1]["cwd"] == "/tmp/workspace"


@pytest.mark.anyio
async def test_command_executor_keeps_output_when_hook_closes_stdin(
    tmp_path,
) -> None:
    script = tmp_path / "fast_hook.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': "
        "'PreToolUse', 'permissionDecision': 'deny', "
        "'permissionDecisionReason': 'fast'}}))\n",
        encoding="utf-8",
    )
    command = subprocess.list2cmdline([sys.executable, str(script)])
    definition = _definitions({
        "PreToolUse": [_hook(command)],
    })[0]

    output = await HookCommandExecutor().execute(
        definition,
        {
            "cwd": str(tmp_path),
            "session_id": "sid",
            "payload": "x" * (1024 * 1024),
        },
    )

    assert output.data["hookSpecificOutput"][
        "permissionDecisionReason"
    ] == "fast"


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
async def test_stop_exit_two_spills_full_stderr_continuation(tmp_path) -> None:
    script = tmp_path / "large_stop_hook.py"
    script.write_text(
        "import sys\n"
        "print('x' * 10000, file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    definition = _definitions({
        "Stop": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
            additional_context_limit=1,
        )],
    })[0]
    executor = HookCommandExecutor(spill_store=HookOutputSpillStore(
        root=tmp_path / "spill",
    ))
    runtime = HookRuntime(
        (definition,),
        command_runner=executor,
    )

    result = await runtime.dispatch(HookEventRequest(
        event="Stop",
        payload={
            "cwd": str(tmp_path),
            "session_id": "sid",
        },
    ))

    record = result.records[0]
    assert record.ok
    assert record.effect.continuation_prompt.startswith(
        "Hook continuation-prompt output spilled to "
    )
    assert record.output["reason"] == record.effect.continuation_prompt
    assert "x" * 5 not in record.effect.continuation_prompt
    assert "size_bytes: 10000" in record.effect.continuation_prompt


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
    assert output.data["stdout"].startswith("HEAD-")
    assert output.data["stdout"].endswith("-TAIL")

    await executor.cleanup_session("sid")

    assert not spill_path.exists()


@pytest.mark.anyio
async def test_command_executor_parses_large_structured_stdout(tmp_path) -> None:
    script = tmp_path / "large_json_hook.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'hookSpecificOutput': {'hookEventName': "
        "'PreToolUse', 'permissionDecision': 'deny', "
        "'permissionDecisionReason': 'x' * 256}}))\n",
        encoding="utf-8",
    )
    definition = _definitions({
        "PreToolUse": [_hook(
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

    assert output.data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output.data["hookSpecificOutput"][
        "permissionDecisionReason"
    ] == "x" * 256
    assert output.data["outputSpill"]["stdout"]["size_bytes"] > 64


@pytest.mark.anyio
async def test_command_executor_uses_spill_summary_for_large_plain_context(
    tmp_path,
    monkeypatch,
) -> None:
    script = tmp_path / "large_context_hook.py"
    script.write_text("print('x' * 256)\n", encoding="utf-8")
    definition = _definitions({
        "SessionStart": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]
    executor = HookCommandExecutor(spill_store=HookOutputSpillStore(
        threshold_bytes=32,
        root=tmp_path / "spill",
    ))
    monkeypatch.setattr(
        hook_command_module,
        "MAX_STRUCTURED_OUTPUT_BYTES",
        64,
    )

    output = await executor.execute(
        definition,
        {"cwd": str(tmp_path), "session_id": "sid"},
    )

    assert output.data["stdout"].startswith("Hook stdout output spilled to ")
    assert output.data["outputSpill"]["stdout"]["size_bytes"] > 64


@pytest.mark.anyio
async def test_command_executor_rejects_stdout_above_parse_limit(
    tmp_path,
    monkeypatch,
) -> None:
    script = tmp_path / "oversized_hook.py"
    script.write_text("print('x' * 256)\n", encoding="utf-8")
    definition = _definitions({
        "PreToolUse": [_hook(
            subprocess.list2cmdline([sys.executable, str(script)]),
        )],
    })[0]
    executor = HookCommandExecutor(spill_store=HookOutputSpillStore(
        threshold_bytes=32,
        root=tmp_path / "spill",
    ))
    monkeypatch.setattr(
        hook_command_module,
        "MAX_STRUCTURED_OUTPUT_BYTES",
        64,
    )

    with pytest.raises(HookCommandError, match="exceeds 64 bytes"):
        await executor.execute(
            definition,
            {"cwd": str(tmp_path), "session_id": "sid"},
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
