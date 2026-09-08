# -*- coding: utf-8 -*-

"""验证 Hook runtime 的匹配、并发、状态发布与资源生命周期。"""


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


def test_discovery_keeps_executable_handler_types_and_skips_unsupported() -> None:
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
    warnings = []
    definitions = resolve_hook_definitions(
        raw,
        source_scope="project",
        source_path=None,
        warnings=warnings,
    )

    assert [item.handler.type for item in definitions] == [
        "command", "mcp_tool",
    ]
    assert "|mcp_tool:files:read:" in definitions[1].key
    assert definitions[1].handler.server == "files"
    assert definitions[1].handler.tool == "read"
    assert len(warnings) == 2


def test_handler_content_changes_update_content_hash() -> None:
    first = resolve_hook_definitions(
        {"PreToolUse": [{"hooks": [{"type": "command", "command": "one"}]}]},
        source_scope="project",
        source_path=None,
    )[0]
    second = resolve_hook_definitions(
        {"PreToolUse": [{"hooks": [{"type": "command", "command": "two"}]}]},
        source_scope="project",
        source_path=None,
    )[0]
    assert first.content_hash != second.content_hash


def test_registry_keeps_mcp_hooks_in_catalog_and_runtime() -> None:
    definitions = resolve_hook_definitions(
        {"PreToolUse": [{"hooks": [
            {"type": "mcp_tool", "server": "files", "tool": "read"},
            {"type": "command", "command": "check"},
        ]}]},
        source_scope="project",
        source_path=None,
    )
    registry = HookRegistry(
        command_runner=_CommandRunner(),
        mcp_runner=_CommandRunner(),
    )
    runtime = registry.build(
        definitions,
        hook_states={
            item.key: {"trusted_hash": item.content_hash}
            for item in definitions
        },
    )
    assert [item.handler.type for item in runtime.definitions] == [
        "mcp_tool",
        "command",
    ]
    assert runtime.installed_count == 2
    assert runtime.active_count == 2
    assert runtime.status().warnings == ()
    snapshot = registry.inspect(
        definitions,
        workspace=Path("."),
    )
    assert [item.handler_type for item in snapshot.hooks] == ["mcp_tool", "command"]


@pytest.mark.anyio
async def test_registry_uses_explicit_hook_resource_lifecycle() -> None:
    cleanup_calls: list[str] = []
    close_calls: list[str] = []

    async def cleanup_session(session_id: str) -> None:
        cleanup_calls.append(session_id)

    async def close() -> None:
        close_calls.append("closed")

    registry = HookRegistry(
        command_runner=_CommandRunner(),
        cleanup_session=cleanup_session,
        close=close,
    )

    await registry.cleanup_session("sid_test")
    await registry.close()

    assert cleanup_calls == ["sid_test"]
    assert close_calls == ["closed"]


def test_registry_does_not_warn_for_supported_mcp_hook_sources(tmp_path) -> None:
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

    assert warnings == ()


@pytest.mark.anyio
async def test_non_session_async_hook_runs_in_owned_background_task() -> None:
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

    assert len(definitions) == 1
    assert warnings == []

    started = asyncio.Event()
    release = asyncio.Event()

    class Runner(object):
        async def execute(self, _definition, _payload):
            started.set()
            await release.wait()
            return HookCommandOutput(data={})

    owner = HookAsyncTaskOwner(max_concurrency=1)
    status = _RecordingHookStatus()
    runtime = HookRuntime(
        definitions,
        command_runner=Runner(),
        async_task_owner=owner,
        status_port=status,
    )
    result = await runtime.dispatch(HookEventRequest(
        event="PreToolUse",
        match_value="shell_command",
        payload={"session_id": "sid_test"},
    ))

    assert len(result.records) == 1
    assert result.records[0].error == ""
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    await owner.close()
    assert [run.status for run in status.started_runs] == ["running"]
    assert [run.status for run in status.completed_runs] == ["completed"]


@pytest.mark.anyio
async def test_async_session_end_hook_runs_synchronously() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class Runner(object):
        async def execute(self, _definition, _payload):
            started.set()
            await release.wait()
            return HookCommandOutput(data={})

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
    assert record.output["reason"] == "0123456789"
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
            return HookCommandOutput(data={})

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
            return HookCommandOutput(data={
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
            return HookCommandOutput(data=_wire_output(
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
