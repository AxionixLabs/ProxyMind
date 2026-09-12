"""通过真实 MCP 连接验证 P2 增量批次、冻结目录与使用范围门禁。"""

import asyncio
import json

import pytest

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from mcp import types as mcp_types
from mcp.shared.exceptions import McpError

from agent.application.hooks.context import HookExecutionContext
from agent.application.turns.commands import TurnApplication
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.application.turns.execution import TurnExecution
from agent.application.turns.run_result import RunResult
from agent.domain.policies import preset_permissions
from agent.harness.execution.resources import ExecutionResources
from agent.harness.execution.turn_runner import TurnRunner
from agent.harness.hooks.async_tasks import HookAsyncTaskOwner
from agent.harness.hooks.runtime import HookRuntime
from agent.harness.hooks.scope import HookExecutionScope
from agent.harness.mcp.owner import McpRuntimeOwner
from agent.harness.sessions.owner import SessionRuntimeOwner
from agent.ports.mcp_runtime import (
    McpRuntimeContext,
    McpServicesBusy,
)
from frontends.subscription.forwarding import AgentExecutor
from frontends.subscription.models import AgentForwardRequest
from infrastructure.errors import AppError
from infrastructure.hooks.discovery import resolve_hook_definitions
from infrastructure.mcp import tool_runtime as tool_runtime_module
from infrastructure.mcp.composite_session import CompositeToolSession
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from infrastructure.mcp.hook_runner import (
    HookMcpError,
    HookMcpRunner,
)
from infrastructure.mcp.tool_runtime import CompositeToolRuntime
from tests.external_mcp.fixtures import (
    read_facts,
    wait_for_fact,
)
from tests.external_mcp.test_service_control import (
    FixtureConfig,
    cleanup,
    control_runtime,
    ping,
    request,
)


def select_servers(config, *keys):
    config.servers = {key: config.servers[key] for key in keys}


def identities(reply):
    return reply.instance_id, reply.pid, reply.session_id


@pytest.mark.anyio
@pytest.mark.parametrize("case_id", [
    "start-fills-missing-enabled", "force-adds-disabled-without-restart",
])
async def test_p0_golden_p2_batches_use_real_connections(control_runtime, fixtures_root, case_id):
    runtime, config = control_runtime
    cases = json.loads((fixtures_root / "mcp" / "control_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in cases if item["id"] == case_id)
    select_servers(config, *(item["config_key"] for item in case["initial"]))
    before = {}
    for entry in case["initial"]:
        key = entry["config_key"]
        config.servers[key]["enabled"] = entry["enabled"]
        if entry["state"] == "ready":
            await runtime.control_service(request(runtime, key, "force"))
            before[key] = await ping(runtime, f"mcp__{key.lower()}__ping")
    await runtime.start(include_disabled=case["request"]["action"] == "force")
    snapshots = {item.config_key: item for item in runtime.service_snapshots}
    for key in case["expected"]["connect"]:
        assert snapshots[key].state == "ready"
        assert (await ping(runtime, snapshots[key].tool_prefix + "ping")).call_count == 1
    for key in case["expected"]["preserve"]:
        assert identities(await ping(runtime, snapshots[key].tool_prefix + "ping")) == identities(before[key])
    for key in case["expected"]["disabled"]:
        assert snapshots[key].state == "stopped" and snapshots[key].config_enabled is False
    assert not any(fact.event == "session.closed" for fact in read_facts(config.workspace / "b.jsonl"))


@pytest.mark.anyio
async def test_all_start_force_are_idempotent_and_restart_drops_temporary_connections(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "A", "B", "D")
    original_config = (config.workspace / "mcp-fixture.toml").read_bytes()
    await runtime.start(include_disabled=True)
    before = {key: await ping(runtime, f"mcp__{key.lower()}__ping") for key in config.servers}
    await runtime.start()
    await runtime.start(include_disabled=True)
    for key in config.servers:
        assert identities(await ping(runtime, f"mcp__{key.lower()}__ping")) == identities(before[key])
    await runtime.control_service(request(runtime, "A", "stop"))
    await runtime.start()
    assert identities(await ping(runtime, "mcp__a__ping")) != identities(before["A"])
    assert identities(await ping(runtime, "mcp__b__ping")) == identities(before["B"])
    assert identities(await ping(runtime, "mcp__d__ping")) == identities(before["D"])
    await runtime.restart()
    assert identities(await ping(runtime, "mcp__b__ping")) != identities(before["B"])
    assert next(item for item in runtime.service_snapshots if item.config_key == "D").state == "stopped"
    assert (config.workspace / "mcp-fixture.toml").read_bytes() == original_config


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["cold", "incremental", "restart"])
async def test_required_failure_retires_only_uncommitted_batch(control_runtime, operation):
    runtime, config = control_runtime
    select_servers(config, "A", "B", "Slow")
    before = None
    if operation != "cold":
        await runtime.control_service(request(runtime, "B", "start"))
        before = await ping(runtime, "mcp__b__ping")
    config.servers["Slow"].update(enabled=True, required=True)
    start = asyncio.create_task(runtime.restart() if operation == "restart" else runtime.start())
    try:
        await wait_for_fact(config.workspace / "a.jsonl", "tools.listed")
        await wait_for_fact(config.workspace / "slow.jsonl", "process.started")
        assert not start.done()
        with runtime.use_tools() as view:
            assert view is not None
            assert all(not name.startswith("mcp__a__") for name in view.tools)
            if operation == "incremental":
                assert "mcp__b__ping" in view.tools
        status = await asyncio.wait_for(runtime.control_service(request(runtime, "Slow", "status")), timeout=0.5)
        assert status.snapshot.state == "starting"
        with pytest.raises(AppError, match="Required MCP"):
            await start
    finally:
        if not start.done():
            start.cancel()
        await asyncio.gather(start, return_exceptions=True)
    group = runtime.group
    assert group is not None
    assert group.owned_keys == (frozenset({"B"}) if operation == "incremental" else frozenset())
    assert all(not name.startswith("mcp__a__") for name in group.tools)
    assert not any(item["state"] in ("ready", "empty") for item in runtime.last_start_snapshot["items"])
    if before is not None and operation == "incremental":
        assert identities(await ping(runtime, "mcp__b__ping")) == identities(before)
    assert any(fact.event == "session.closed" for fact in read_facts(config.workspace / "a.jsonl"))


@pytest.mark.anyio
async def test_optional_failure_publishes_successful_batch_and_invalid_restart_preserves_it(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "A", "F")
    config.servers["F"]["enabled"] = True
    await runtime.start()
    before = await ping(runtime, "mcp__a__ping")
    assert {item.config_key: item.state for item in runtime.service_snapshots} == {"A": "ready", "F": "failed"}
    config.servers["F"]["command"] = 42
    with pytest.raises((AppError, ValueError)):
        await runtime.restart()
    assert identities(await ping(runtime, "mcp__a__ping")) == identities(before)
    await runtime.stop_services()
    assert runtime.group is None


@pytest.mark.anyio
async def test_busy_full_operations_have_no_partial_effect_and_single_targets_are_isolated(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "A", "B")
    await runtime.start()
    before = await ping(runtime, "mcp__b__ping")
    with runtime.use_tools("b") as view:
        assert view is not None and set(view.tools) == {"mcp__b__ping", "mcp__b__block"}
        for operation in (runtime.stop_services, runtime.restart):
            with pytest.raises(McpServicesBusy) as error:
                await operation()
            assert error.value.config_keys == ("B",)
        assert {item.state for item in runtime.service_snapshots} == {"ready"}
        for action in ("stop", "restart"):
            result = await runtime.control_service(request(runtime, "B", action))
            assert result.outcome == "busy" and result.snapshot.state == "ready"
        assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "applied"
        assert identities(await ping(runtime, "mcp__b__ping")) == identities(before)
    await runtime.stop_services()
    with pytest.raises(RuntimeError, match="scope has ended"):
        await view.call_tool("mcp__b__ping")


@pytest.mark.anyio
async def test_disconnected_frozen_scope_cannot_be_rebound_or_replayed(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "Disconnect")
    await runtime.start(include_disabled=True)
    with runtime.use_tools() as view:
        with pytest.raises(McpError):
            await view.call_tool("mcp__disconnect__ping")
        result = await runtime.control_service(request(runtime, "Disconnect", "force"))
        assert result.outcome == "busy" and result.snapshot.state == "failed"
        with pytest.raises(McpServicesBusy):
            await runtime.start(include_disabled=True)
        with pytest.raises(KeyError):
            await view.call_tool("mcp__disconnect__ping")
    assert sum(fact.event == "tool.started" for fact in read_facts(config.workspace / "disconnect.jsonl")) == 1


def make_resources(runtime):
    registry = SimpleNamespace(
        list_tools=lambda: mcp_types.ListToolsResult(tools=[]),
        has_tool=lambda _name: False,
    )
    return ExecutionResources(
        event_reporting=SimpleNamespace(close=AsyncMock()),
        tool_runtime_builder=CompositeToolRuntime,
        client_registry_factory=lambda: registry,
        builtin_registry_factory=lambda: registry,
        external_runtime_factory=lambda: runtime,
        await_cleanup=cleanup,
    )


def make_execution(workspace, source):
    agent = AgentContext.root("session")
    if source == "subagent":
        agent = agent.child("explore", "inspect", agent_id="child")
    context = TurnContext.create(
        agent=agent, cid="conversation", sid="session", source=source,
        pref_config={}, cwd=str(workspace), permissions=preset_permissions("auto"), turn_id="turn_mcp_scope",
    )
    return TurnExecution(
        context=context, message="inspect",
        hook_scope=HookExecutionScope(
            context=HookExecutionContext.from_turn(context), dispatcher=HookRuntime.empty(),
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("source", ["tui", "subagent", "subscription"])
async def test_turn_and_subscription_scopes_freeze_catalog_and_hold_gate(control_runtime, source):
    runtime, config = control_runtime
    select_servers(config, "A", "D")
    resources = make_resources(runtime)
    await resources.external_mcp.start()
    entered = asyncio.Event()
    release = asyncio.Event()
    execution = make_execution(config.workspace, source)
    runner = TurnRunner(resources)

    async def operation(prepared, session, tools, _report):
        names = {item["name"] for item in tools}
        assert names == {"mcp__a__ping", "mcp__a__block"}
        descriptor = session.mcp_approval_descriptor("mcp__a__ping", {})
        entered.set()
        await release.wait()
        assert {item.name for item in (await session.list_tools()).tools} == names
        assert session.mcp_approval_descriptor("mcp__a__ping", {}) == descriptor
        result = await session.call_tool("mcp__a__ping", {}, call_id="call-original", turn_context=prepared.context)
        assert not result.isError
        with pytest.raises(KeyError):
            await session.call_tool("mcp__d__ping", {}, call_id="call-missing", turn_context=prepared.context)
        return RunResult(status="completed")

    async def run_bound(_host=None, **_kwargs):
        return await runner({}, execution, operation, event_report=SimpleNamespace())

    subscription = None
    if source == "subscription":
        subscription = AgentExecutor(
            run_bound, turn_application=TurnApplication(runtime_factory=SessionRuntimeOwner),
            environment_snapshot_provider=lambda _host: {"snapshot_id": "test-environment"},
        )
        client = SimpleNamespace(send_mind_started=AsyncMock(), send_mind_completed=AsyncMock(), send_mind_failed=AsyncMock())
        task = asyncio.create_task(subscription.execute(
            SimpleNamespace(history_workspace=str(config.workspace)), client, SimpleNamespace(),
            SimpleNamespace(session_id="subscription"),
            AgentForwardRequest(message_id="message", call_id="call", cid="conversation", sid="session", payload={"message": "inspect"}),
        ))
    else:
        task = asyncio.create_task(run_bound())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(McpServicesBusy):
            await resources.external_mcp.stop_services()
        await resources.external_mcp.start(include_disabled=True)
        assert (await runtime.control_service(request(runtime, "A", "restart"))).outcome == "busy"
        release.set()
        await task
        if source == "subscription":
            client.send_mind_completed.assert_awaited_once()
        with resources.external_mcp.use_tools() as view:
            assert "mcp__d__ping" in view.tools
        await resources.external_mcp.restart()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if subscription is not None:
            await subscription.close()
        await resources.close()


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["success", "cancel", "timeout"])
async def test_real_hook_holds_only_its_service_until_completion(control_runtime, ending):
    runtime, config = control_runtime
    select_servers(config, "A", "B")
    resources = make_resources(runtime)
    await resources.external_mcp.start()
    definition = resolve_hook_definitions(
        {"UserPromptSubmit": [{"hooks": [{"type": "mcp_tool", "server": "a", "tool": "block"}]}]},
        source_scope="project", source_path=None,
    )[0]
    if ending == "timeout":
        definition = replace(definition, handler=replace(definition.handler, timeout_sec=1))
    runner = HookMcpRunner(resources.external_mcp.use_tools)
    task = asyncio.create_task(runner.execute(definition, {}))
    try:
        await wait_for_fact(config.workspace / "a.jsonl", "tool.started")
        assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "busy"
        assert (await runtime.control_service(request(runtime, "B", "restart"))).outcome == "applied"
        if ending == "success":
            (config.workspace / "a.release").touch()
            output = await task
            assert output.data["call_count"] == 1
        elif ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(HookMcpError, match="timed out"):
                await task
        assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "applied"
        assert sum(fact.event == "tool.started" for fact in read_facts(config.workspace / "a.jsonl")) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await resources.close()


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["start", "restart"])
async def test_cancelled_batch_collects_new_processes_and_preserves_only_preexisting(control_runtime, operation):
    runtime, config = control_runtime
    select_servers(config, "A", "B", "Slow")
    await runtime.control_service(request(runtime, "B", "start"))
    before = await ping(runtime, "mcp__b__ping")
    config.servers["Slow"]["enabled"] = True
    task = asyncio.create_task(runtime.start() if operation == "start" else runtime.restart())
    await wait_for_fact(config.workspace / "slow.jsonl", "process.started")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.group.owned_keys == (frozenset({"B"}) if operation == "start" else frozenset())
    if operation == "start":
        assert identities(await ping(runtime, "mcp__b__ping")) == identities(before)


@pytest.mark.anyio
async def test_final_close_waits_for_borrowed_scope_and_rejects_new_usage(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "A")
    owner = McpRuntimeOwner(runtime_factory=lambda: runtime)
    await owner.start()
    with owner.use_tools() as view:
        closing = asyncio.create_task(owner.close())
        async with asyncio.timeout(2):
            while owner.current is not None:
                await asyncio.sleep(0)
        assert not closing.done()
        with pytest.raises(RuntimeError, match="cleanup is still pending"):
            await owner.start()
        with owner.use_tools() as unavailable:
            assert unavailable is None
        with runtime.use_tools() as unavailable:
            assert unavailable is None
        result = await view.call_tool("mcp__a__ping")
        assert not result.isError
        closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert runtime.group is None
    assert any(fact.event == "session.closed" for fact in read_facts(config.workspace / "a.jsonl"))


@pytest.mark.anyio
async def test_old_workspace_batch_cannot_publish_after_detach(control_runtime, tmp_path):
    runtime, config = control_runtime
    select_servers(config, "A", "Slow")
    config.servers["Slow"]["enabled"] = True
    owner = McpRuntimeOwner(runtime_factory=lambda: runtime)
    task = asyncio.create_task(owner.start())
    await wait_for_fact(config.workspace / "slow.jsonl", "process.started")
    old = owner.detach()
    assert old is runtime
    second_config = FixtureConfig(tmp_path / "next", {"A": config.servers["A"]}, config.repository)
    second_config.workspace.mkdir()
    second = ExternalMcpRuntime(McpRuntimeContext(second_config, AsyncMock(), AsyncMock(), cleanup))
    try:
        await second.start()
        current = await ping(second, "mcp__a__ping")
        with pytest.raises(RuntimeError, match="no longer active"):
            await task
        assert runtime.group.owned_keys == frozenset() and not runtime.group.tools
        assert identities(await ping(second, "mcp__a__ping")) == identities(current)
        assert (await second.control_service(request(runtime, "A", "stop"))).outcome == "failed"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await second.stop()


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["single", "all"])
async def test_no_new_scope_can_enter_between_stop_precheck_and_close(control_runtime, monkeypatch, scope):
    runtime, config = control_runtime
    select_servers(config, "A", "B")
    await runtime.start()
    group = runtime.group
    entered = asyncio.Event()
    release = asyncio.Event()
    close = group.stop_service if scope == "single" else group.close

    async def held_close(*args):
        entered.set()
        await release.wait()
        await close(*args)

    monkeypatch.setattr(group, "stop_service" if scope == "single" else "close", held_close)
    task = asyncio.create_task(
        runtime.control_service(request(runtime, "A", "stop")) if scope == "single" else runtime.stop_services(),
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        with runtime.use_tools() as view:
            assert "mcp__a__ping" not in view.tools
            assert ("mcp__b__ping" in view.tools) == (scope == "single")
            release.set()
            await task
            if scope == "single":
                assert not (await view.call_tool("mcp__b__ping")).isError
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("failure_phase", ["catalog", "before_user_flow", "operation", "cancel"])
async def test_tool_scope_releases_on_catalog_and_consumer_failure(control_runtime, monkeypatch, failure_phase):
    runtime, config = control_runtime
    select_servers(config, "A")
    resources = make_resources(runtime)
    await resources.external_mcp.start()
    entered = asyncio.Event()
    original_build = tool_runtime_module.build_tool_context

    async def build(**kwargs):
        if failure_phase == "catalog":
            assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "busy"
            raise ValueError("catalog failed")
        return await original_build(**kwargs)

    async def before_user_flow():
        if failure_phase == "before_user_flow":
            assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "busy"
            raise ValueError("before_user_flow failed")

    async def operation(_session, _tools):
        entered.set()
        if failure_phase == "cancel":
            await asyncio.Future()
        raise ValueError("operation failed")

    monkeypatch.setattr(tool_runtime_module, "build_tool_context", build)
    task = asyncio.create_task(resources.with_mcp_session({}, operation, before_user_flow))
    try:
        if failure_phase == "cancel":
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert (await runtime.control_service(request(runtime, "A", "restart"))).outcome == "busy"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ValueError, match=failure_phase):
                await task
        assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "applied"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await resources.close()


@pytest.mark.anyio
async def test_background_hook_and_nested_scope_release_before_final_close(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "A")
    resources = make_resources(runtime)
    await resources.external_mcp.start()
    hooks = HookAsyncTaskOwner()
    definition = resolve_hook_definitions(
        {"UserPromptSubmit": [{"hooks": [{"type": "mcp_tool", "server": "a", "tool": "block"}]}]},
        source_scope="project", source_path=None,
    )[0]
    assert hooks.submit(HookMcpRunner(resources.external_mcp.use_tools).execute(definition, {}), name="test MCP hook")
    try:
        await wait_for_fact(config.workspace / "a.jsonl", "tool.started")
        with resources.external_mcp.use_tools():
            assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "busy"
        assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "busy"
        (config.workspace / "a.release").touch()
        await hooks.close()
        assert (await runtime.control_service(request(runtime, "A", "stop"))).outcome == "applied"
    finally:
        (config.workspace / "a.release").touch()
        await hooks.close()
        await resources.close()


@pytest.mark.anyio
async def test_workspace_release_failure_keeps_real_connection_for_retry(control_runtime, monkeypatch):
    runtime, config = control_runtime
    select_servers(config, "A")
    owner = McpRuntimeOwner(runtime_factory=lambda: runtime)
    await owner.start()
    group = runtime.group
    original_close = group.close
    close = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    monkeypatch.setattr(group, "close", close)
    assert owner.detach() is runtime
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await owner.release(runtime)
    with pytest.raises(RuntimeError, match="cleanup is still pending"):
        await owner.start()
    with pytest.raises(RuntimeError, match="cleanup is still pending"):
        await owner.stop_services()
    assert group.owned_keys == frozenset({"A"})
    monkeypatch.setattr(group, "close", original_close)
    await owner.close()
    assert runtime.group is None
    assert any(fact.event == "session.closed" for fact in read_facts(config.workspace / "a.jsonl"))


@pytest.mark.anyio
async def test_restart_preserves_approval_service_keys_and_cannot_reuse_old_scope(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "Docs API", "Docs/API")
    await runtime.start(include_disabled=True)
    with runtime.use_tools() as view:
        session = CompositeToolSession(external_group=view)
        descriptors = {
            name: session.mcp_approval_descriptor(name, {})
            for name in view.tools if name.endswith("__ping")
        }
        assert {descriptor.config_server_key for descriptor in descriptors.values()} == {"Docs API", "Docs/API"}
        assert len({descriptor.server for descriptor in descriptors.values()}) == 2
        with pytest.raises(McpServicesBusy):
            await runtime.restart()
    for server in config.servers.values():
        server["enabled"] = True
    await runtime.restart()
    with runtime.use_tools() as refreshed:
        current = CompositeToolSession(external_group=refreshed)
        for name, descriptor in descriptors.items():
            assert current.mcp_approval_descriptor(name, {}) == descriptor
            with pytest.raises(RuntimeError, match="scope has ended"):
                await view.call_tool(name)


@pytest.mark.anyio
async def test_cancelled_interactive_stop_reports_completed_after_real_release(control_runtime, monkeypatch):
    runtime, config = control_runtime
    select_servers(config, "A")
    await runtime.start()
    group = runtime.group
    closing = asyncio.Event()
    release = asyncio.Event()
    original_close = group.close

    async def close():
        closing.set()
        await release.wait()
        await original_close()

    monkeypatch.setattr(group, "close", close)
    task = asyncio.create_task(runtime.stop_services())
    await asyncio.wait_for(closing.wait(), timeout=2)
    task.cancel()
    release.set()
    await task
    assert runtime.group is None
    assert any(fact.event == "session.closed" for fact in read_facts(config.workspace / "a.jsonl"))
