# -*- coding: utf-8 -*-

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import types as mcp_types

from agent.application.approvals.models import ApprovalOutcome
from agent.application.tools.coding import coding_tools
from agent.application.tools.javascript import (
    _authorize_nested_tool,
    _js_repl_arguments,
    javascript_tools,
)
from agent.application.tools.coding_schemas import JS_REPL_INPUT_SCHEMA
from agent.application.tools.context import ToolHandlerContext
from agent.application.tools.authorization import ToolTurnInterrupted
from infrastructure.mcp.local_tool_registry import ToolRegistry
from infrastructure.mcp.local_tool_factory import build_client_tool_registry
from infrastructure.mcp.composite_session import CompositeToolSession
from infrastructure.mcp.nested_tool_results import _nested_tool_response
from mind import (
    create_javascript_provider,
    create_workspace_coding,
)
from infrastructure.config.execution_policy_manager import ExecPolicyManager
from agent.ports.capabilities import SandboxMode
from agent.ports.javascript import (
    JavaScriptExecution,
    JavaScriptExecutionError,
    JavaScriptExecutionRequest,
    JavaScriptResetDisposition,
    NestedToolDispatch,
)
from infrastructure.sidecars.javascript.process import (
    STDERR_TAIL_MAX_BYTES,
    append_stderr_tail,
    stderr_tail_bytes,
)
from infrastructure.sidecars.javascript.protocol import FRAME_MAX_BYTES
from infrastructure.sidecars.javascript.provider import JavaScriptSidecarProvider
from infrastructure.platform.images import FileImageReader
from agent.application.views import NativeToolResultView, ToolStartView
from frontends.terminal.traces.native import render_tool_result_entries
from agent.application.turns.context import AgentContext, ToolInvocation, TurnContext
from agent.application.hooks.models import (
    HookVisibleToolResult,
    ToolCallRunResult,
)
from agent.harness.tools.client_calls import ClientToolCallRunner
from infrastructure.mcp.tool_execution import McpToolExecutionAdapter
from infrastructure.mcp.nested_tool_results import nested_tool_output
from agent.composition import open_effect_journal
from agent.application.config.settings import FeatureSettings
from agent.domain.policies import preset_permissions


async def _execute(
    provider: JavaScriptSidecarProvider,
    session_id: str,
    code: str,
    *,
    cwd: str | Path | None,
    timeout_ms: int,
    call_tool: NestedToolDispatch,
    access_mode: SandboxMode = "workspace-write",
) -> JavaScriptExecution:
    """使用公开具名请求调用测试中的 Sidecar Provider。"""
    return await provider.execute(
        request=JavaScriptExecutionRequest(
            session_id=session_id,
            code=code,
            cwd=os.fspath(cwd or provider.root),
            access_mode=access_mode,
            timeout_ms=timeout_ms,
        ),
        call_tool=call_tool,
    )


def _require_node() -> None:
    node_path = shutil.which(os.environ.get("JS_REPL_NODE_PATH") or "node")
    if not node_path:
        pytest.fail("Node is required for js_repl tests")
    output = subprocess.run(
        [node_path, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", output)
    if match is None:
        pytest.fail(f"Unable to parse Node version: {output!r}")
    if tuple(map(int, match.groups())) < (22, 22, 0):
        pytest.fail("Node 22.22.0 or newer is required for js_repl tests")


def test_js_repl_timeout_contract_and_stderr_tail_match_upstream() -> None:
    assert _js_repl_arguments({"code": "", "timeout_ms": 0})["timeout_ms"] == 0
    assert _js_repl_arguments({"code": "", "timeout_ms": 180_000})[
        "timeout_ms"
    ] == 180_000
    assert JS_REPL_INPUT_SCHEMA["properties"]["timeout_ms"]["minimum"] == 0
    assert "maximum" not in JS_REPL_INPUT_SCHEMA["properties"]["timeout_ms"]

    lines = deque()
    append_stderr_tail(lines, "甲" * 400)
    for index in range(30):
        append_stderr_tail(lines, f"latest-{index}-" + "x" * 500)

    assert len(lines) <= 20
    assert stderr_tail_bytes(lines) <= STDERR_TAIL_MAX_BYTES
    assert lines[-1].startswith("latest-29-")


@pytest.mark.anyio
async def test_js_repl_persists_bindings_and_bridges_tools_and_images(
    tmp_path: Path,
) -> None:
    _require_node()
    calls = []

    async def call_tool(name, arguments, call_id):
        calls.append((name, arguments, call_id))
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"{name}:{arguments['value']}",
        }

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        first = await _execute(pool,
            "session:root",
            "const value = 4; console.log(value);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        process = pool._sessions["session:root"]._process
        second = await _execute(pool,
            "session:root",
            "console.log(value + 3); console.log((await host.tool('probe', {value: 8})).output);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        silent = await _execute(pool,
            "session:root",
            "await host.tool('probe', {value: 9});",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        explicit = await _execute(pool,
            "session:root",
            "await host.tool('probe', {value: 10}); console.log('explicit');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        multiple = await _execute(pool,
            "session:root",
            "await host.tool('probe', {value: 11}); "
            "await host.tool('probe', {value: 12});",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        assert pool._sessions["session:root"]._process is process
        image = await _execute(pool,
            "session:root",
            "await host.emitImage('data:image/png;base64,AA=='); console.log('sent');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert first.output == "4"
    assert second.output == "7\nprobe:8"
    assert calls[0][0:2] == ("probe", {"value": 8})
    assert silent.output == ""
    assert explicit.output == "explicit"
    assert multiple.output == ""
    assert image.output == "sent"
    assert image.attachments == ({
        "kind": "image",
        "mime_type": "image/png",
        "data_url": "data:image/png;base64,AA==",
        "detail": "high",
    },)


@pytest.mark.anyio
async def test_js_repl_preserves_initialized_bindings_after_cell_error(
    tmp_path: Path,
) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        with pytest.raises(JavaScriptExecutionError, match="expected failure"):
            await _execute(pool,
                "sid-failed-cell",
                "const committedBeforeFailure = 9; throw new Error('expected failure');",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
        persisted = await _execute(pool,
            "sid-failed-cell",
            "console.log(committedBeforeFailure);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        with pytest.raises(JavaScriptExecutionError, match="Top-level static import"):
            await _execute(pool,
                "sid-failed-cell",
                "import fs from 'node:fs';",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
    finally:
        await pool.close()

    assert persisted.output == "9"


@pytest.mark.anyio
async def test_js_repl_persists_complex_bindings_and_failed_cell_writes(
    tmp_path: Path,
) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        await _execute(pool,
            "sid-complex-bindings",
            "let mutable = 2; var legacy = 3; "
            "function double(value) { return value * 2; } "
            "class Box { constructor(value) { this.value = value; } } "
            "const {left, nested: {right}} = {left: 4, nested: {right: 5}};",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        updated = await _execute(pool,
            "sid-complex-bindings",
            "mutable += 10; legacy++; "
            "console.log(mutable, legacy, double(left), new Box(right).value);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        persisted_update = await _execute(pool,
            "sid-complex-bindings",
            "console.log(mutable, legacy);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )

        with pytest.raises(JavaScriptExecutionError, match="commit selected bindings"):
            await _execute(pool,
                "sid-complex-bindings",
                "let failedLet = 8; var failedVar = 9; "
                "function failedFunction() { return 10; } "
                "class FailedClass { static value() { return 11; } } "
                "const [failedLeft, ...failedRest] = [12, 13, 14]; "
                "futureAssigned = 15; var futureAssigned; "
                "for (var loopValue of [16]) { break; } "
                "throw new Error('commit selected bindings'); "
                "var unreachedVar = 17; "
                "function unreachedFunction() { return 18; } "
                "class UnreachedClass {}",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
        committed = await _execute(pool,
            "sid-complex-bindings",
            "console.log(failedLet, failedVar, failedFunction(), "
            "FailedClass.value(), failedLeft, failedRest.join(','), "
            "futureAssigned, loopValue); "
            "console.log(typeof unreachedVar, typeof unreachedFunction, "
            "typeof UnreachedClass);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert updated.output == "12 4 8 5"
    assert persisted_update.output == "12 4"
    assert committed.output == (
        "8 9 10 11 12 13,14 15 16\nundefined undefined undefined"
    )


@pytest.mark.anyio
async def test_js_repl_sessions_are_isolated_and_reset_lazily(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        assert (
            await pool.reset_session("sid-a")
            is JavaScriptResetDisposition.NOT_STARTED
        )
        assert pool._sessions == {}

        await _execute(pool,
            "sid-a",
            "const sessionValue = 41;",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        isolated = await _execute(pool,
            "sid-b",
            "console.log(typeof sessionValue);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )

        session = pool._sessions["sid-a"]
        process = session._process.process
        assert process is not None
        assert (
            await pool.reset_session("sid-a")
            is JavaScriptResetDisposition.RESET
        )
        assert session._process.process is None
        assert process.returncode is not None

        restarted = await _execute(pool,
            "sid-a",
            "console.log(typeof sessionValue);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert isolated.output == "undefined"
    assert restarted.output == "undefined"


@pytest.mark.anyio
async def test_js_repl_serializes_same_session_without_blocking_other_sessions(
    tmp_path: Path,
) -> None:
    _require_node()
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_tool(name, arguments, call_id):
        assert name == "wait"
        assert arguments == {}
        started.set()
        await release.wait()
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": "released",
        }

    async def unexpected_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        running = asyncio.create_task(_execute(pool,
            "sid-serialized",
            "const serialValue = 41; await host.tool('wait', {});",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=blocking_tool,
        ))
        await asyncio.wait_for(started.wait(), timeout=2)

        queued = asyncio.create_task(_execute(pool,
            "sid-serialized",
            "console.log(serialValue + 1);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=unexpected_tool,
        ))
        await asyncio.sleep(0)
        resetting = asyncio.create_task(pool.reset_session("sid-serialized"))

        independent = await asyncio.wait_for(_execute(pool,
            "sid-independent",
            "console.log('independent');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=unexpected_tool,
        ), timeout=2)
        assert not queued.done()
        assert not resetting.done()

        release.set()
        first, second, reset = await asyncio.gather(running, queued, resetting)
        after_reset = await _execute(pool,
            "sid-serialized",
            "console.log(typeof serialValue);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=unexpected_tool,
        )
    finally:
        release.set()
        await pool.close()

    assert independent.output == "independent"
    assert first.output == ""
    assert second.output == "42"
    assert reset is JavaScriptResetDisposition.RESET
    assert after_reset.output == "undefined"


@pytest.mark.anyio
async def test_js_repl_close_session_only_closes_target(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        await _execute(pool,
            "sid-a",
            "const valueA = 1;",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        await _execute(pool,
            "sid-b",
            "const valueB = 2;",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )

        assert await pool.close_session("sid-a") is None
        preserved = await _execute(pool,
            "sid-b",
            "console.log(valueB);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        recreated = await _execute(pool,
            "sid-a",
            "console.log(typeof valueA);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert preserved.output == "2"
    assert recreated.output == "undefined"


@pytest.mark.anyio
async def test_js_repl_waits_for_unawaited_tool_calls(tmp_path: Path) -> None:
    _require_node()
    completed = asyncio.Event()

    async def call_tool(name, arguments, call_id):
        await asyncio.sleep(0.05)
        completed.set()
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"{name}:{arguments['value']}",
        }

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        result = await _execute(pool,
            "sid-unawaited",
            "void host.tool('probe', {value: 8}); console.log('cell-complete');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert result.output == "cell-complete"
    assert completed.is_set()


@pytest.mark.anyio
async def test_js_repl_timeout_resets_kernel(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        with pytest.raises(JavaScriptExecutionError, match="timed out; kernel reset"):
            await _execute(pool,
                "session:root",
                "await new Promise(() => {});",
                cwd=tmp_path,
                timeout_ms=50,
                call_tool=call_tool,
            )
        recovered = await _execute(pool,
            "session:root",
            "console.log(typeof value, 'recovered');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert recovered.output == "undefined recovered"


@pytest.mark.anyio
async def test_js_repl_zero_timeout_resets_kernel(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        await _execute(pool,
            "sid-zero-timeout",
            "const beforeTimeout = 1;",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        with pytest.raises(JavaScriptExecutionError, match="timed out; kernel reset"):
            await _execute(pool,
                "sid-zero-timeout",
                "console.log(beforeTimeout);",
                cwd=tmp_path,
                timeout_ms=0,
                call_tool=call_tool,
            )
        recovered = await _execute(pool,
            "sid-zero-timeout",
            "console.log(typeof beforeTimeout);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert recovered.output == "undefined"


@pytest.mark.anyio
async def test_js_repl_reads_output_frames_larger_than_default_stream_limit(
    tmp_path: Path,
) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        result = await _execute(pool,
            "sid-large-frame",
            "console.log('x'.repeat(100000));",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        recovered = await _execute(pool,
            "sid-large-frame",
            "console.log('recovered');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert len(result.output) == 100000
    assert FRAME_MAX_BYTES > len(result.output)
    assert recovered.output == "recovered"


@pytest.mark.anyio
async def test_js_repl_matches_module_and_local_import_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_node()
    monkeypatch.setenv(
        "JS_REPL_NODE_PATH",
        str(tmp_path / "missing-node-executable"),
    )
    module_path = tmp_path / "local-value.mjs"
    module_path.write_text("export const value = 1;\n", encoding="utf-8")

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        first = await _execute(pool,
            "sid-imports",
            "const firstLocal = await import('./local-value.mjs'); "
            "console.log(typeof process, firstLocal.value, "
            "typeof (await import('node:fs')).readFile);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        for specifier in (
            "node:process",
            "child_process",
            "node:child_process",
            "worker_threads",
            "node:worker_threads",
        ):
            with pytest.raises(
                JavaScriptExecutionError,
                match=re.escape(
                    f'Importing module "{specifier}" is not allowed in js_repl'
                ),
            ):
                await _execute(pool,
                    "sid-imports",
                    f"await import({json.dumps(specifier)});",
                    cwd=tmp_path,
                    timeout_ms=5000,
                    call_tool=call_tool,
                )

        module_path.write_text("export const value = 2;\n", encoding="utf-8")
        reloaded = await _execute(pool,
            "sid-imports",
            "console.log((await import('./local-value.mjs')).value);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert first.output == "undefined 1 function"
    assert reloaded.output == "2"


@pytest.mark.anyio
async def test_js_repl_resolves_nested_files_packages_and_module_boundaries(
    tmp_path: Path,
) -> None:
    _require_node()
    nested_path = tmp_path / "nested.mjs"
    nested_path.write_text("export const nested = 4;\n", encoding="utf-8")
    (tmp_path / "entry.mjs").write_text(
        "import {nested} from './nested.mjs'; export const combined = nested + 1;\n",
        encoding="utf-8",
    )
    (tmp_path / "data.json").write_text('{"value": 1}\n', encoding="utf-8")
    (tmp_path / "module-directory").mkdir()

    package_root = tmp_path / "node_modules" / "repl-fixture"
    package_root.mkdir(parents=True)
    (package_root / "package.json").write_text(json.dumps({
        "name": "repl-fixture",
        "type": "module",
        "exports": {
            ".": "./index.js",
            "./feature": "./feature.js",
        },
    }), encoding="utf-8")
    (package_root / "index.js").write_text(
        "export const packageValue = 6;\n",
        encoding="utf-8",
    )
    (package_root / "feature.js").write_text(
        "export const featureValue = 7;\n",
        encoding="utf-8",
    )

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        resolved = await _execute(pool,
            "sid-module-boundaries",
            "const entry = await import('./entry.mjs'); "
            f"const fileModule = await import({json.dumps(nested_path.as_uri())}); "
            "const packageModule = await import('repl-fixture'); "
            "const feature = await import('repl-fixture/feature'); "
            "console.log(entry.combined, fileModule.nested, "
            "packageModule.packageValue, feature.featureValue, "
            "import.meta.main, import.meta.resolve('./entry.mjs').startsWith('file:'));",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        with pytest.raises(
            JavaScriptExecutionError,
            match="Directory imports are not supported",
        ):
            await _execute(pool,
                "sid-module-boundaries",
                "await import('./module-directory');",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
        with pytest.raises(
            JavaScriptExecutionError,
            match="Only .js and .mjs files are supported",
        ):
            await _execute(pool,
                "sid-module-boundaries",
                "await import('./data.json');",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
        with pytest.raises(JavaScriptExecutionError, match="Unsupported import specifier"):
            await _execute(pool,
                "sid-module-boundaries",
                "await import('https://example.com/module.js');",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
    finally:
        await pool.close()

    assert resolved.output == "5 4 6 7 true true"


@pytest.mark.anyio
async def test_js_repl_enforces_filesystem_access_mode(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    outside_path = tmp_path.parent / "js-repl-outside-denied.txt"
    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        workspace = await _execute(pool,
            "sid-permissions",
            "const fs = await import('node:fs'); "
            "fs.writeFileSync('./inside.txt', 'ok'); "
            f"try {{ fs.writeFileSync({json.dumps(str(outside_path))}, 'blocked'); }} "
            "catch (error) { console.log(error.code); } "
            "const permissionBinding = 1;",
            cwd=tmp_path,
            access_mode="workspace-write",
            timeout_ms=5000,
            call_tool=call_tool,
        )
        read_only = await _execute(pool,
            "sid-permissions",
            "const fs = await import('node:fs'); "
            "try { fs.writeFileSync('./read-only-denied.txt', 'blocked'); } "
            "catch (error) { console.log(error.code); } "
            "fs.writeFileSync(host.tmpDir + '/scratch.txt', 'ok'); "
            "console.log(typeof permissionBinding, fs.existsSync(host.tmpDir + '/scratch.txt'));",
            cwd=tmp_path,
            access_mode="read-only",
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert workspace.output == "ERR_ACCESS_DENIED"
    assert read_only.output == "ERR_ACCESS_DENIED\nundefined true"
    assert (tmp_path / "inside.txt").read_text(encoding="utf-8") == "ok"
    assert not (tmp_path / "read-only-denied.txt").exists()
    assert not outside_path.exists()


@pytest.mark.anyio
async def test_js_repl_persisted_helpers_require_an_active_cell(tmp_path: Path) -> None:
    _require_node()
    calls = []

    async def call_tool(name, arguments, call_id):
        calls.append((name, arguments, call_id))
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"{name}:{arguments['value']}",
        }

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        await _execute(pool,
            "sid-helper",
            "const savedTool = host.tool; "
            "globalThis.lateToolError = 'pending'; "
            "setTimeout(() => savedTool('late', {value: 1})"
            ".catch(error => { lateToolError = error.message; }), 10);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        result = await _execute(pool,
            "sid-helper",
            "await new Promise(resolve => setTimeout(resolve, 50)); "
            "console.log((await savedTool('active', {value: 2})).output); "
            "console.log(lateToolError);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert result.output == "active:2\njs_repl exec context not found"
    assert [(name, arguments) for name, arguments, _ in calls] == [
        ("active", {"value": 2}),
    ]


@pytest.mark.anyio
async def test_js_repl_uncaught_async_error_restarts_kernel(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        await _execute(pool,
            "sid-fatal",
            "const doomed = 1; setTimeout(() => { throw new Error('fatal'); }, 10);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        session = pool._sessions["sid-fatal"]
        process = session._process.process
        assert process is not None
        await asyncio.wait_for(process.wait(), timeout=2)
        recovered = await _execute(pool,
            "sid-fatal",
            "console.log(typeof doomed);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert recovered.output == "undefined"


@pytest.mark.anyio
async def test_js_repl_cancellation_resets_kernel(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        running = asyncio.create_task(_execute(pool,
            "session:root",
            "await new Promise(() => {});",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        ))
        await asyncio.sleep(0.05)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        recovered = await _execute(pool,
            "session:root",
            "console.log('recovered');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert recovered.output == "recovered"


@pytest.mark.anyio
async def test_js_repl_recovers_after_kernel_exit(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        await _execute(pool,
            "sid-exit",
            "const oldValue = 1;",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        session = pool._sessions["sid-exit"]
        process = session._process.process
        assert process is not None
        process.kill()
        await process.wait()

        recovered = await _execute(pool,
            "sid-exit",
            "console.log(typeof oldValue, 'recovered');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert recovered.output == "undefined recovered"


@pytest.mark.anyio
async def test_js_repl_kernel_exit_waits_for_started_tool_calls(tmp_path: Path) -> None:
    _require_node()
    started = asyncio.Event()
    release = asyncio.Event()

    async def call_tool(name, arguments, call_id):
        started.set()
        await release.wait()
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": "finished",
        }

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        running = asyncio.create_task(_execute(pool,
            "sid-exit-tool",
            "await host.tool('slow', {});",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        ))
        await asyncio.wait_for(started.wait(), timeout=2)
        session = pool._sessions["sid-exit-tool"]
        process = session._process.process
        assert process is not None
        process.kill()
        await process.wait()
        await asyncio.sleep(0)
        assert not running.done()

        release.set()
        with pytest.raises(JavaScriptExecutionError, match="kernel exited unexpectedly"):
            await asyncio.wait_for(running, timeout=2)
    finally:
        release.set()
        await pool.close()


@pytest.mark.anyio
async def test_js_repl_client_tool_executes_without_shell_metadata(
    tmp_path: Path,
) -> None:
    _require_node()
    coding = create_workspace_coding(root=tmp_path, application_layout=None)
    javascript = create_javascript_provider(
        workspace_root=tmp_path,
        application_layout=None,
    )
    registry = build_client_tool_registry(
        coding,
        javascript=javascript,
        image_reader=FileImageReader(tmp_path),
        features=FeatureSettings(js_repl=True),
    )
    session = CompositeToolSession(client_registry=registry)
    turn = TurnContext.create(
        agent=AgentContext.root("sid_root"),
        cid="cid_root",
        sid="sid_root",
        source="test",
        pref_config={},
        cwd=str(tmp_path),
        permissions=preset_permissions("auto"),
        turn_id="turn_root",
    )
    image_path = tmp_path / "pixel.png"
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
    ))
    code = (
        f"const out = await host.tool('view_image', {{path: {json.dumps(str(image_path))}}}); "
        "await host.emitImage(out); console.log(out.type);"
    )
    arguments = {"code": code, "timeout_ms": 5000}
    try:
        result = await session.call_tool(
            "js_repl",
            arguments,
            call_id="call_js_repl",
            turn_context=turn,
            pref_config={},
        )
        reset = await session.call_tool(
            "js_repl_reset",
            {},
            call_id="call_js_repl_reset",
            turn_context=turn,
            pref_config={},
        )
        after_reset = await session.call_tool(
            "js_repl",
            {"code": "console.log(typeof out);", "timeout_ms": 5000},
            call_id="call_js_repl_after_reset",
            turn_context=turn,
            pref_config={},
        )
    finally:
        await javascript.close()
        await coding.close()

    assert result.isError is False
    assert result.structuredContent["data"]["output"] == "function_call_output"
    assert result.structuredContent["attachments"][0]["mime_type"] == "image/png"
    assert reset.isError is False
    assert reset.structuredContent["data"]["reset"] is True
    assert after_reset.structuredContent["data"]["output"] == "undefined"


@pytest.mark.anyio
async def test_js_repl_nested_shell_uses_local_approval(tmp_path: Path) -> None:
    _require_node()
    events = []

    class Coordinator:
        def __init__(self) -> None:
            self.requests = []

        async def request_outcome(self, approval):
            self.requests.append(approval)
            events.append("approval")
            return ApprovalOutcome.create(
                "accept",
                source="user",
                reason="user",
            )

    coordinator = Coordinator()
    coding = create_workspace_coding(root=tmp_path, application_layout=None)
    javascript = create_javascript_provider(
        workspace_root=tmp_path,
        application_layout=None,
    )
    coding.shell_command = AsyncMock(return_value=coding.ok_result(
        "nested shell completed",
        output="nested-ok",
    ))
    exec_policy_manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        writable_rules_path=tmp_path / ".mind" / "rules" / "default.rules",
    )
    registry = ToolRegistry([
        *javascript_tools(
            javascript,
            approval_coordinator=coordinator,
            execution_policy=exec_policy_manager,
        ),
        *coding_tools(coding),
    ])
    session = CompositeToolSession(client_registry=registry)
    turn = TurnContext.create(
        agent=AgentContext.root("sid_nested"),
        cid="cid_nested",
        sid="sid_nested",
        source="test",
        pref_config={},
        cwd=str(tmp_path),
        permissions=preset_permissions("auto"),
        turn_id="turn_nested",
    )
    arguments = {
        "code": "await host.tool('shell_command', {command: 'rm -rf nested'});",
        "timeout_ms": 5000,
    }

    async def dispatch_nested(tool, args, call_id):
        events.append("dispatch")
        result = await session.call_tool(
            tool,
            args,
            call_id=call_id,
            turn_context=turn,
            pref_config={},
        )
        return nested_tool_output(
            session,
            tool_name=tool,
            result=result,
            call_id=call_id,
        )

    try:
        result = await session.call_tool(
            "js_repl",
            arguments,
            call_id="call_nested",
            turn_context=turn,
            pref_config={},
            meta={"_nested_tool_dispatch": dispatch_nested},
        )
    finally:
        await javascript.close()
        await coding.close()

    assert result.isError is False
    assert result.structuredContent["data"]["output"] == ""
    assert "nested shell completed" not in result.structuredContent["data"]["output"]
    assert events == ["approval", "dispatch"]
    assert coordinator.requests[0]["tool"] == "shell_command"
    assert coordinator.requests[0]["command"] == "rm -rf nested"
    coding.shell_command.assert_awaited_once()


@pytest.mark.anyio
async def test_nested_approval_cancel_interrupts_turn(tmp_path: Path) -> None:
    class Coordinator:
        async def request_outcome(self, _approval):
            return ApprovalOutcome.create(
                "cancel",
                source="user",
                reason="user",
            )

    turn = TurnContext.create(
        agent=AgentContext.root("sid_nested_cancel"),
        cid="cid_nested_cancel",
        sid="sid_nested_cancel",
        source="test",
        pref_config={},
        cwd=str(tmp_path),
        permissions=preset_permissions("auto"),
        turn_id="turn_nested_cancel",
    )
    interrupt = AsyncMock(return_value=True)
    runtime = ToolHandlerContext(
        session=SimpleNamespace(),
        turn_context=turn,
        pref_config={},
        interrupt_turn=interrupt,
    )
    manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        writable_rules_path=tmp_path / ".mind" / "rules" / "default.rules",
    )

    with pytest.raises(ToolTurnInterrupted):
        await _authorize_nested_tool(
            runtime,
            tool="shell_command",
            arguments={"command": "rm -rf nested"},
            approval_coordinator=Coordinator(),
            execution_policy=manager,
            call_id="nested-cancel",
        )

    interrupt.assert_awaited_once_with("nested-cancel")


@pytest.mark.anyio
async def test_js_repl_nested_shell_stays_inside_javascript_trace_after_approval(
    tmp_path: Path,
) -> None:
    _require_node()
    events = []

    class Approval:
        async def request_outcome(self, approval):
            events.append(("approval", approval["command"]))
            return ApprovalOutcome.create(
                "accept",
                source="user",
                reason="user",
            )

    class Presentation:
        async def emit(self, view):
            events.append(("view", view))

    class Output:
        def record_tool_arguments(self, name, arguments, *, call_id=None):
            events.append(("arguments", name))

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

    coding = create_workspace_coding(root=tmp_path, application_layout=None)
    javascript = create_javascript_provider(
        workspace_root=tmp_path,
        application_layout=None,
    )
    coding.shell_command = AsyncMock(return_value=coding.ok_result(
        "nested shell completed",
        command='Start-Process "https://example.com"',
        output="nested-ok",
        output_lines=["nested-ok"],
        exit_code=0,
    ))
    exec_policy_manager = ExecPolicyManager(
        workspace_root=tmp_path,
        rules_paths=(),
        writable_rules_path=tmp_path / ".mind" / "rules" / "default.rules",
    )
    registry = ToolRegistry([
        *javascript_tools(
            javascript,
            approval_coordinator=Approval(),
            execution_policy=exec_policy_manager,
        ),
        *coding_tools(coding),
    ])
    session = CompositeToolSession(client_registry=registry)
    turn = TurnContext.create(
        agent=AgentContext.root("sid_nested_trace"),
        cid="cid_nested_trace",
        sid="sid_nested_trace",
        source="test",
        pref_config={},
        cwd=str(tmp_path),
        permissions=preset_permissions("auto"),
        turn_id="turn_nested_trace",
    )
    runner = ClientToolCallRunner(
        session=session,
        output_control=Output(),
        presentation=Presentation(),
        tools=[
            {"name": "js_repl", "meta": {"client_builtin": True}},
            {"name": "shell_command", "meta": {"client_builtin": True}},
        ],
        pref_config={},
        tool_call_coordinator=SimpleNamespace(
            run_invocation=AsyncMock(side_effect=run_allowed),
        ),
        tool_execution=McpToolExecutionAdapter(),
        activity=SimpleNamespace(
            tool_started=AsyncMock(),
            tool_completed=AsyncMock(),
            approval_started=AsyncMock(),
            approval_completed=AsyncMock(),
        ),
        effect_journal=open_effect_journal(tmp_path / "effects.db"),
    )

    try:
        outcome = await runner.execute(
            ToolInvocation(
                turn=turn,
                call_id="call-js",
                name="js_repl",
                arguments={
                    "code": (
                        'await host.tool("shell_command", {'
                        'command: \'Start-Process "https://example.com"\''
                        '});'
                    ),
                    "timeout_ms": 5000,
                },
            ),
            use_coding_trace=True,
        )
    finally:
        await javascript.close()
        await coding.close()

    assert events[0] == ("arguments", "js_repl")
    assert isinstance(events[1][1], ToolStartView)
    assert events[1][1].name == "js_repl"
    assert events[2] == (
        "approval",
        'Start-Process "https://example.com"',
    )
    assert [event for event in events if event[0] == "arguments"] == [
        ("arguments", "js_repl"),
    ]
    views = [value for kind, value in events if kind == "view"]
    assert [view.name for view in views if isinstance(view, ToolStartView)] == [
        "js_repl",
    ]
    native_views = [
        view for view in views if isinstance(view, NativeToolResultView)
    ]
    assert [view.name for view in native_views] == [
        "js_repl",
    ]
    titles = [
        entry.title
        for view in native_views
        for entry in render_tool_result_entries(
            view.name,
            view.arguments,
            ok=view.ok,
            data=view.data,
            cost_ms=view.cost_ms,
        )
    ]
    assert all("Running" not in title for title in titles)
    assert all("Ran" not in title for title in titles)
    coding.shell_command.assert_awaited_once()
    assert outcome.result.fields["data"]["output"] == ""


def test_js_repl_function_bridge_preserves_mixed_image_content() -> None:
    result = mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(type="text", text="image note"),
            mcp_types.ImageContent(
                type="image",
                data="AA==",
                mimeType="image/png",
                _meta={"repl/imageDetail": "low"},
            ),
        ],
        isError=False,
    )

    response = _nested_tool_response(result, call_id="call-mixed")

    assert response == {
        "type": "function_call_output",
        "call_id": "call-mixed",
        "output": [
            {"type": "input_text", "text": "image note"},
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,AA==",
                "detail": "low",
            },
        ],
    }


@pytest.mark.anyio
async def test_js_repl_mcp_bridge_preserves_type_and_image_rules(
    tmp_path: Path,
) -> None:
    _require_node()

    class ServiceSession:
        async def call_tool(self, name, arguments, **kwargs):
            _ = arguments, kwargs
            content = [
                mcp_types.ImageContent(
                    type="image",
                    data="AA==",
                    mimeType="image/png",
                ),
            ]
            if name == "mixed_mcp":
                content.insert(0, mcp_types.TextContent(
                    type="text",
                    text="image note",
                ))
            return mcp_types.CallToolResult(content=content, isError=False)

    coding = create_workspace_coding(root=tmp_path, application_layout=None)
    javascript = create_javascript_provider(
        workspace_root=tmp_path,
        application_layout=None,
    )
    registry = ToolRegistry([
        *javascript_tools(javascript),
        *coding_tools(coding),
    ])
    session = CompositeToolSession(
        service_session=ServiceSession(),
        client_registry=registry,
    )
    turn = TurnContext.create(
        agent=AgentContext.root("sid_mcp_image"),
        cid="cid_mcp_image",
        sid="sid_mcp_image",
        source="test",
        pref_config={},
        cwd=str(tmp_path),
        permissions=preset_permissions("full-access"),
        turn_id="turn_mcp_image",
    )
    try:
        mixed = await session.call_tool(
            "js_repl",
            {
                "code": (
                    "const mixed = await host.tool('mixed_mcp', {}); "
                    "console.log(mixed.type, mixed.output.content.length); "
                    "try { await host.emitImage(mixed); } "
                    "catch (error) { console.log(error.message); }"
                ),
                "timeout_ms": 5000,
            },
            call_id="call_mixed_mcp",
            turn_context=turn,
            pref_config={},
        )
        image = await session.call_tool(
            "js_repl",
            {
                "code": "await host.emitImage(await host.tool('image_mcp', {}));",
                "timeout_ms": 5000,
            },
            call_id="call_image_mcp",
            turn_context=turn,
            pref_config={},
        )
    finally:
        await javascript.close()
        await coding.close()

    mixed_output = mixed.structuredContent["data"]["output"]
    assert "mcp_tool_call_output 2" in mixed_output
    assert "does not accept mixed text and image content" in mixed_output
    assert mixed.structuredContent["attachments"] == []
    assert image.structuredContent["data"]["output"] == ""
    assert image.structuredContent["attachments"] == [{
        "kind": "image",
        "mime_type": "image/png",
        "data_url": "data:image/png;base64,AA==",
        "detail": "high",
    }]


@pytest.mark.anyio
async def test_js_repl_emits_byte_and_multiple_images(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        result = await _execute(pool,
            "sid-images",
            "const savedEmitImage = host.emitImage; "
            "await savedEmitImage({bytes: new Uint8Array([1, 2]), "
            "mimeType: 'image/png', detail: 'original'}); "
            "await host.emitImage('DATA:image/jpeg;base64,AA==');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        persisted = await _execute(pool,
            "sid-images",
            "await savedEmitImage('data:image/webp;base64,AA==');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        with pytest.raises(JavaScriptExecutionError, match="does not accept mixed text and image"):
            await _execute(pool,
                "sid-images",
                "await host.emitImage({type: 'function_call_output', output: ["
                "{type: 'input_text', text: 'caption'}, "
                "{type: 'input_image', image_url: 'data:image/png;base64,AA=='}]});",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
    finally:
        await pool.close()

    assert result.attachments == (
        {
            "kind": "image",
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,AQI=",
            "detail": "original",
        },
        {
            "kind": "image",
            "mime_type": "image/jpeg",
            "data_url": "DATA:image/jpeg;base64,AA==",
            "detail": "high",
        },
    )
    assert persisted.attachments[0]["mime_type"] == "image/webp"


@pytest.mark.anyio
async def test_js_repl_waits_for_unawaited_image_and_tracks_errors(
    tmp_path: Path,
) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        raise AssertionError((name, arguments, call_id))

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        emitted = await _execute(pool,
            "sid-background-image",
            "void host.emitImage('data:image/png;base64,AA=='); "
            "console.log('cell-complete');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        with pytest.raises(JavaScriptExecutionError, match="expected non-empty bytes"):
            await _execute(pool,
                "sid-background-image",
                "void host.emitImage({bytes: new Uint8Array(), mimeType: 'image/png'}); "
                "console.log('unreachable');",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
        caught = await _execute(pool,
            "sid-background-image",
            "try { await host.emitImage({bytes: new Uint8Array(), "
            "mimeType: 'image/png'}); } "
            "catch (error) { console.log(error.message); } "
            "console.log('cell-complete');",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
    finally:
        await pool.close()

    assert emitted.output == "cell-complete"
    assert emitted.attachments == ({
        "kind": "image",
        "mime_type": "image/png",
        "data_url": "data:image/png;base64,AA==",
        "detail": "high",
    },)
    assert "expected non-empty bytes" in caught.output
    assert "cell-complete" in caught.output
    assert caught.attachments == ()


@pytest.mark.anyio
async def test_js_repl_only_attaches_explicit_valid_images(tmp_path: Path) -> None:
    _require_node()

    async def call_tool(name, arguments, call_id):
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": [{
                "type": "input_image",
                "image_url": "data:image/png;base64,AA==",
            }],
        }

    pool = JavaScriptSidecarProvider(tmp_path)
    try:
        tool_only = await _execute(pool,
            "sid-explicit-image",
            "const imageResult = await host.tool('image', {}); console.log(imageResult.type);",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        image_only = await _execute(pool,
            "sid-explicit-image",
            "await host.tool('image', {});",
            cwd=tmp_path,
            timeout_ms=5000,
            call_tool=call_tool,
        )
        with pytest.raises(JavaScriptExecutionError, match="only accepts data URLs"):
            await _execute(pool,
                "sid-explicit-image",
                "void host.emitImage('https://example.com/image.png');",
                cwd=tmp_path,
                timeout_ms=5000,
                call_tool=call_tool,
            )
    finally:
        await pool.close()

    assert tool_only.output == "function_call_output"
    assert tool_only.attachments == ()
    assert image_only.output == ""
    assert image_only.attachments == ()
