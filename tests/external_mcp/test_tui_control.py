"""用共享菜单、正式控制端口和真实 MCP 子进程验证 P3 交互接线。"""

import asyncio
import json
from dataclasses import (
    asdict,
    replace,
)
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jsonschema import Draft202012Validator

from agent.harness.mcp.owner import McpRuntimeOwner
from agent.ports.mcp_runtime import McpSingleService
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features import mcp
from tests.external_mcp.fixtures import read_facts
from tests.external_mcp.test_batch_lifecycle import (
    identities,
    select_servers,
)
from tests.external_mcp.test_service_control import (
    control_runtime,
    ping,
    request,
    single_control,
)


def ui(runtime):
    owner = McpRuntimeOwner(runtime_factory=lambda: runtime)
    assert owner.snapshot.runtime_id == runtime.runtime_id
    views = []
    host = SimpleNamespace(
        execution=SimpleNamespace(external_mcp=owner),
        activity=SimpleNamespace(enabled=False),
        frontend=SimpleNamespace(
            runtime=TuiRuntime(),
            application=SimpleNamespace(emit=views.append, viewport=SimpleNamespace(width=120)),
        ),
    )
    return host, views


@pytest.mark.anyio
async def test_menu_all_is_single_real_service_and_direct_start_preserves_other_connection(control_runtime, fixtures_root):
    runtime, config = control_runtime
    select_servers(config, "all", "B", "D")
    host, views = ui(runtime)
    await single_control(runtime, request(runtime, "B", "start"))
    before = identities(await ping(runtime, "mcp__b__ping"))
    chooser = asyncio.create_task(mcp.choose_mcp_action(host.frontend.runtime, host))
    await asyncio.sleep(0)
    menu = host.frontend.runtime.screen.menu
    root_options = menu.state.request.options
    menu._choose_index(next(index for index, option in enumerate(root_options) if option.value == McpSingleService("all")))
    menu._choose_index(1)
    command = await chooser
    result = await mcp.run_mcp_action(host, command)
    assert len(result.services) == 1
    assert result.services[0].snapshot.state == "ready"
    assert result.services[0].config_key == "all"
    assert identities(await ping(runtime, "mcp__b__ping")) == before
    assert next(item for item in runtime.snapshot.services if item.config_key == "D").state == "stopped"
    schema = json.loads((fixtures_root / "mcp" / "control.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    validator.evolve(schema={"$ref": "#/$defs/McpMenuSelection"}).validate(json.loads(json.dumps(asdict(command))))
    validator.evolve(schema={"$ref": "#/$defs/McpControlResult"}).validate(json.loads(json.dumps(asdict(result))))
    assert not menu.active
    for action in ("start", "force", "restart", "stop"):
        result = await mcp.run_mcp_action(host, mcp.all_mcp_request(host, action))
        validator.evolve(schema={"$ref": "#/$defs/McpControlResult"}).validate(json.loads(json.dumps(asdict(result))))
        assert {item.config_key for item in result.services} == {"all", "B", "D"}
        if action in ("start", "force"):
            assert identities(await ping(runtime, "mcp__b__ping")) == before
            assert next(item for item in result.services if item.config_key == "B").outcome == "unchanged"
        if action == "restart":
            assert next(item for item in result.services if item.config_key == "D").outcome == "disabled"
        mcp.render_mcp_action_result(host, result)
    assert len([view for view in views if view.type == "tui.external_mcp.status"]) == 4
    assert "all services" in views[-2].renderable.plain_text
    assert not runtime.started


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", (True, False))
@pytest.mark.parametrize("all_services", (True, False))
async def test_invalid_config_keeps_known_connections_visible_and_stoppable(control_runtime, enabled, all_services):
    runtime, config = control_runtime
    select_servers(config, "A")
    config.servers["A"]["enabled"] = enabled
    host, views = ui(runtime)
    await mcp.run_mcp_action(host, mcp.all_mcp_request(host, "start" if enabled else "force"))
    before = tuple(read_facts(config.workspace / "a.jsonl"))
    with patch.object(config, "load", side_effect=ValueError("invalid config")):
        mcp.render_mcp_status(host)
        text = "".join(value for _, value in views[0].renderable.fragments)
        assert "Config error: ValueError: invalid config" in text and "Connection: ready" in text
        expected_config = "enabled" if enabled else "disabled (temporary connection)"
        assert f"Config: {expected_config}" in text
        assert runtime.snapshot.services[0].config_enabled is enabled
        assert tuple(read_facts(config.workspace / "a.jsonl")) == before
        command = mcp.all_mcp_request(host, "stop") if all_services else request(runtime, "A", "stop")
        outcome = await mcp.run_mcp_action(host, command)
        assert outcome.services[0].snapshot.state == "stopped"
        assert outcome.services[0].snapshot.config_enabled is enabled
        mcp.render_mcp_status(host)
    assert not runtime.started


@pytest.mark.anyio
async def test_all_snapshot_does_not_wait_for_mutation_and_stale_requests_never_control_replacement(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "A")
    host, views = ui(runtime)
    command = mcp.all_mcp_request(host, "status")
    async with runtime._lifecycle_lock:
        result = await asyncio.wait_for(runtime.control(command), timeout=0.5)
        assert result.services[0].snapshot.state == "stopped"
    for changed in (replace(command, runtime_id="old"), replace(command, workspace="/old")):
        with pytest.raises(RuntimeError, match="no longer active"):
            await runtime.control(changed)
    assert not read_facts(config.workspace / "a.jsonl")
    config.workspace = config.workspace / "replacement"
    mcp.render_mcp_status(host, command)
    assert "no longer active" in views[-2].renderable.plain_text


@pytest.mark.anyio
async def test_start_activity_reuses_snapshot_and_carries_raw_single_scope(control_runtime):
    runtime, config = control_runtime
    select_servers(config, "Docs API")
    host, _ = ui(runtime)
    snapshots = []

    async def capture(provider):
        snapshots.append(provider())

    runtime._context.start_activity.side_effect = capture
    result = await mcp.run_mcp_action(host, request(runtime, "Docs API", "force"))
    assert result.services[0].snapshot.state == "ready"
    assert snapshots[0]["summary"] == "External MCP · Docs API · starting"
    assert snapshots[0]["items"] and snapshots[0]["done"] is False
    runtime._context.stop_activity.assert_not_awaited()
