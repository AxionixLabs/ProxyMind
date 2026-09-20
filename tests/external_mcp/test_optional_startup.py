# -*- coding: utf-8 -*-

import asyncio
import sys
import time
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import McpError

from agent.ports.mcp_runtime import (
    McpRuntimeContext,
    McpServicesBusy,
)
from frontends.terminal.mcp_status import external_mcp_status_view
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from tests.external_mcp.fixtures import (
    FixtureSpec,
    read_facts,
    wait_for_fact,
)
from tests.external_mcp.test_service_control import (
    FixtureConfig,
    cleanup,
)


@pytest.fixture
async def delayed_runtime(tmp_path, repository_root):
    spec = FixtureSpec(tmp_path, "delayed", mode="delayed-discovery", repository=repository_root)
    config = FixtureConfig(tmp_path, {"delayed": {
        "command": sys.executable, "args": list(spec.arguments()), "cwd": str(repository_root),
        "startup_timeout_sec": 10, "optional_startup_wait_sec": 0.05,
    }}, repository_root)
    runtime = ExternalMcpRuntime(McpRuntimeContext(config, AsyncMock(), AsyncMock(), cleanup))
    try:
        yield runtime, config, spec
    finally:
        await runtime.stop()


async def ready(runtime):
    async with asyncio.timeout(10):
        while not runtime.started:
            await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_optional_startup_returns_early_coalesces_and_only_enriches_next_scope(delayed_runtime):
    runtime, config, spec = delayed_runtime
    started = time.monotonic()
    await runtime.start()
    assert time.monotonic() - started < 1
    assert runtime.service_snapshots[0].state == "starting"
    presentation = external_mcp_status_view(runtime.last_start_snapshot)
    assert presentation.level == "running" and "background" in presentation.summary
    with runtime.use_tools() as old:
        assert old is not None and not old.tools
        await asyncio.gather(runtime.start(), runtime.start(), runtime.start())
        await wait_for_fact(spec.facts_path, "fault.injected")
        assert len([fact for fact in read_facts(spec.facts_path) if fact.event == "process.started"]) == 1
        spec.release_path.write_text("ready", encoding="utf-8")
        await ready(runtime)
        assert not old.tools
        with runtime.use_tools() as current:
            assert current is not None and "mcp__delayed__ping" in current.tools
            assert not (await current.call_tool("mcp__delayed__ping")).isError


@pytest.mark.anyio
@pytest.mark.parametrize("changed", [False, True])
async def test_cached_preview_waits_for_same_connection_and_checks_live_version(delayed_runtime, changed):
    runtime, config, spec = delayed_runtime
    spec.release_path.write_text("ready", encoding="utf-8")
    await runtime.start()
    await ready(runtime)
    await runtime.stop_services()
    spec.release_path.unlink()
    offset = len(read_facts(spec.facts_path))
    await runtime.start()
    assert not runtime.started
    assert runtime.service_snapshots[0].tools == ()
    with runtime.use_tools() as cached:
        assert cached is not None and "mcp__delayed__ping" in cached.tools
        tool = cached.tools["mcp__delayed__ping"]
        assert tool.annotations is None and tool.meta["approval_mode"] == "prompt"
        with pytest.raises(McpServicesBusy):
            await runtime.stop_services()
        await runtime.start()
        call = asyncio.create_task(cached.call_tool("mcp__delayed__ping"))
        try:
            await wait_for_fact(spec.facts_path, "fault.injected", after=offset)
            assert not call.done()
            spec.release_path.write_text("changed" if changed else "ready", encoding="utf-8")
            if changed:
                with pytest.raises(McpError, match="catalog changed"):
                    await call
                assert not [fact for fact in read_facts(spec.facts_path)[offset:] if fact.event == "tool.started"]
            else:
                assert not (await call).isError
        finally:
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
    with pytest.raises(RuntimeError, match="scope has ended"):
        await cached.call_tool("mcp__delayed__ping")


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["retire", "config", "disable", "stop", "cancel"])
async def test_pending_startup_retires_without_late_publication(delayed_runtime, outcome):
    runtime, config, spec = delayed_runtime
    if outcome == "cancel":
        config.servers["delayed"]["optional_startup_wait_sec"] = 0
        task = asyncio.create_task(runtime.start())
        await wait_for_fact(spec.facts_path, "fault.injected")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await runtime.start()
        await wait_for_fact(spec.facts_path, "fault.injected")
        if outcome == "retire":
            runtime.retire()
        elif outcome == "config":
            config.servers["delayed"]["enabled_tools"] = []
        elif outcome == "disable":
            config.servers["delayed"]["enabled"] = False
        elif outcome == "stop":
            await runtime.stop_services()
        spec.release_path.write_text("ready", encoding="utf-8")
        if outcome in ("retire", "config", "disable"):
            async with asyncio.timeout(10):
                while runtime.group.owned_keys:
                    await asyncio.sleep(0.01)
    assert runtime.group is None or not runtime.group.tools
    assert runtime.group is None or not runtime.group.owned_keys


@pytest.mark.anyio
async def test_required_service_ignores_optional_budget(delayed_runtime):
    runtime, config, spec = delayed_runtime
    config.servers["delayed"]["required"] = True
    task = asyncio.create_task(runtime.start())
    try:
        await wait_for_fact(spec.facts_path, "fault.injected")
        assert not task.done()
        spec.release_path.write_text("ready", encoding="utf-8")
        await task
        assert runtime.started
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_background_batches_share_stdio_concurrency(delayed_runtime):
    runtime, config, first = delayed_runtime
    second = FixtureSpec(first.directory, "second", mode="delayed-discovery", repository=first.repository)
    await runtime.start()
    await wait_for_fact(first.facts_path, "fault.injected")
    config.servers["second"] = {**config.servers["delayed"], "args": list(second.arguments())}
    await runtime.start()
    assert not read_facts(second.facts_path)
    first.release_path.write_text("ready", encoding="utf-8")
    await wait_for_fact(second.facts_path, "fault.injected")
    second.release_path.write_text("ready", encoding="utf-8")
    async with asyncio.timeout(10):
        while any(item.state == "starting" for item in runtime.service_snapshots):
            await asyncio.sleep(0.01)
    assert all(item.state == "ready" for item in runtime.service_snapshots)


@pytest.mark.anyio
async def test_cancelled_cached_call_keeps_shared_startup_alive(delayed_runtime):
    runtime, config, spec = delayed_runtime
    spec.release_path.write_text("ready", encoding="utf-8")
    await runtime.start()
    await ready(runtime)
    await runtime.stop_services()
    spec.release_path.unlink()
    await runtime.start()
    with runtime.use_tools() as cached:
        assert cached is not None and cached.tools
        call = asyncio.create_task(cached.call_tool("mcp__delayed__ping"))
        await asyncio.sleep(0)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
    assert runtime.group.owned_keys == frozenset({"delayed"})
    spec.release_path.write_text("ready", encoding="utf-8")
    await ready(runtime)
    with runtime.use_tools() as current:
        assert not (await current.call_tool("mcp__delayed__ping")).isError
