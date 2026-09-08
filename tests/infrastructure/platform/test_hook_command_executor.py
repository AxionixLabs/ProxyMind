# -*- coding: utf-8 -*-

"""验证平台 Hook 命令进程、JSON 边界、超时与输出溢写。"""


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
async def test_command_executor_requires_json_for_interrupt_stdout(tmp_path) -> None:
    script = tmp_path / "interrupt_hook.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'systemMessage': 'cancelled'}))\n",
        encoding="utf-8",
    )
    command = subprocess.list2cmdline([sys.executable, str(script)])
    definition = _definitions({
        "Interrupt": [_hook(command)],
    })[0]

    output = await HookCommandExecutor().execute(
        definition,
        {"cwd": str(tmp_path)},
    )

    assert output.data == {"systemMessage": "cancelled"}

    invalid_script = tmp_path / "invalid_interrupt_hook.py"
    invalid_script.write_text("print('not-json')\n", encoding="utf-8")
    invalid_definition = _definitions({
        "Interrupt": [_hook(
            subprocess.list2cmdline([sys.executable, str(invalid_script)]),
        )],
    })[0]

    with pytest.raises(HookCommandError, match="invalid JSON"):
        await HookCommandExecutor().execute(
            invalid_definition,
            {"cwd": str(tmp_path)},
        )


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

    executor = HookCommandExecutor()
    result = await HookRuntime(
        (definition,),
        command_runner=executor,
        context_spiller=executor,
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
        context_spiller=executor,
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
    assert record.output["reason"].startswith("x")
    assert record.output["reason"] != record.effect.continuation_prompt
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
