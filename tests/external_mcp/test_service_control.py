"""通过正式运行时和真实传输验收 P1 单服务控制。"""

import asyncio
import json
import tomllib

import pytest

from collections.abc import Awaitable
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path
from unittest.mock import AsyncMock

from jsonschema import Draft202012Validator
from mcp import types as mcp_types
from mcp.shared.exceptions import McpError
from pydantic import JsonValue

from agent.harness.mcp.owner import McpRuntimeOwner
from agent.ports.mcp_runtime import (
    McpAction,
    McpRuntimeContext,
    McpServiceControlRequest,
    McpSingleService,
)
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from tests.external_mcp.fixtures import (
    FixtureReply,
    FixtureSpec,
    FixtureTransport,
    read_facts,
    remote_fixture,
    wait_for_fact,
    write_config,
)


@dataclass
class FixtureConfig:
    """由测试注入有效配置及工作区，不修改进程环境或用户文件。"""

    workspace: Path
    servers: dict[str, dict[str, JsonValue]]
    repository: Path

    def load(self) -> dict[str, JsonValue]:
        """返回当前测试配置，允许用例显式模拟配置变更。"""
        return {"mcp_servers": dict(self.servers)}


async def cleanup(awaitable: Awaitable[None]) -> None:
    """模拟 Harness 的清理屏障，取消后仍等待资源释放。"""
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def request(runtime: ExternalMcpRuntime, key: str, action: McpAction) -> McpServiceControlRequest:
    """从当前实例冻结明确的单服务请求。"""
    return McpServiceControlRequest(runtime.runtime_id, str(runtime.workspace), action, McpSingleService(key))


async def ping(runtime: ExternalMcpRuntime, name: str) -> FixtureReply:
    """读取真实工具返回的进程和连接身份。"""
    group = runtime.group
    assert group is not None
    result = await group.call_tool(name)
    assert not result.isError
    content = result.content[0]
    assert isinstance(content, mcp_types.TextContent)
    return FixtureReply.model_validate_json(content.text)


@pytest.fixture
async def control_runtime(tmp_path, repository_root):
    path = write_config(tmp_path, (), repository=repository_root)
    config = FixtureConfig(tmp_path, tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"], repository_root)
    runtime = ExternalMcpRuntime(McpRuntimeContext(config, AsyncMock(), AsyncMock(), cleanup))
    try:
        yield runtime, config
    finally:
        await runtime.stop()


@pytest.mark.anyio
async def test_single_stop_restart_and_last_service_start_preserve_other_connection(control_runtime) -> None:
    runtime, _ = control_runtime
    for key in ("A", "B"):
        assert (await runtime.control_service(request(runtime, key, "start"))).outcome == "applied"
    a = await ping(runtime, "mcp__a__ping")
    b = await ping(runtime, "mcp__b__ping")
    assert (await runtime.control_service(request(runtime, "A", "stop"))).snapshot.state == "stopped"
    assert (await ping(runtime, "mcp__b__ping")).session_id == b.session_id
    await runtime.control_service(request(runtime, "A", "restart"))
    assert (await ping(runtime, "mcp__a__ping")).instance_id != a.instance_id
    assert (await ping(runtime, "mcp__b__ping")).instance_id == b.instance_id
    for key in ("A", "B"):
        await runtime.control_service(request(runtime, key, "stop"))
    assert not runtime.started
    assert not runtime.group.tools
    assert (await runtime.control_service(request(runtime, "A", "start"))).snapshot.state == "ready"


@pytest.mark.anyio
async def test_disabled_force_is_idempotent_and_restart_respects_configuration(control_runtime) -> None:
    runtime, config = control_runtime
    before = json.dumps(config.load(), sort_keys=True)
    assert (await runtime.control_service(request(runtime, "D", "start"))).outcome == "disabled"
    assert not (config.workspace / "disabled.jsonl").exists()
    forced = await runtime.control_service(request(runtime, "D", "force"))
    assert forced.snapshot.config_enabled is False and forced.snapshot.state == "ready"
    first = await ping(runtime, "mcp__d__ping")
    assert (await runtime.control_service(request(runtime, "D", "force"))).outcome == "unchanged"
    assert (await runtime.control_service(request(runtime, "D", "start"))).outcome == "unchanged"
    assert (await ping(runtime, "mcp__d__ping")).instance_id == first.instance_id
    result = await runtime.control_service(request(runtime, "D", "restart"))
    assert result.outcome == "disabled" and result.snapshot.state == "stopped"
    assert json.dumps(config.load(), sort_keys=True) == before


@pytest.mark.anyio
@pytest.mark.parametrize("key,discovered,filtered", [("E", 0, 0), ("NoTools", 0, 0), ("Filtered", 2, 2)])
async def test_valid_empty_catalog_is_ready_without_tools(control_runtime, key, discovered, filtered) -> None:
    runtime, _ = control_runtime
    result = await runtime.control_service(request(runtime, key, "start"))
    assert result.outcome == "applied" and runtime.started
    assert result.snapshot.state == "ready"
    assert result.snapshot.tools == ()
    assert (result.snapshot.discovered, result.snapshot.filtered) == (discovered, filtered)


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["F", "Slow", "DiscoveryFailure"])
async def test_single_required_failure_preserves_healthy_service(control_runtime, key) -> None:
    runtime, config = control_runtime
    await runtime.control_service(request(runtime, "B", "start"))
    before = await ping(runtime, "mcp__b__ping")
    config.servers[key]["required"] = True
    result = await runtime.control_service(request(runtime, key, "force"))
    assert result.outcome == "failed"
    assert result.snapshot.state == "failed" and result.snapshot.connection_error
    assert result.snapshot.tools == ()
    assert (await ping(runtime, "mcp__b__ping")).instance_id == before.instance_id
    assert (await runtime.control_service(request(runtime, key, "stop"))).snapshot.state == "stopped"


@pytest.mark.anyio
async def test_status_and_invalid_restart_do_not_touch_healthy_connection(control_runtime) -> None:
    runtime, config = control_runtime
    await runtime.control_service(request(runtime, "A", "start"))
    facts = read_facts(config.workspace / "a.jsonl")
    for _ in range(3):
        result = await runtime.control_service(request(runtime, "A", "status"))
        assert result.snapshot.state == "ready"
    assert read_facts(config.workspace / "a.jsonl") == facts
    config.servers["A"]["url"] = "https://user:secret@example.test/mcp?token=private"
    result = await runtime.control_service(request(runtime, "A", "restart"))
    assert result.outcome == "failed" and result.snapshot.state == "ready"
    assert "secret" not in result.operation_error and "private" not in result.operation_error
    assert (await runtime.control_service(request(runtime, "A", "stop"))).snapshot.state == "stopped"


@pytest.mark.anyio
async def test_removed_configuration_can_only_be_stopped_or_inspected(control_runtime) -> None:
    runtime, config = control_runtime
    await runtime.control_service(request(runtime, "A", "start"))
    del config.servers["A"]
    result = await runtime.control_service(request(runtime, "A", "status"))
    assert result.snapshot.config_enabled is None and result.snapshot.state == "ready"
    for action in ("start", "force", "restart"):
        assert (await runtime.control_service(request(runtime, "A", action))).outcome == "failed"
    assert (await runtime.control_service(request(runtime, "A", "stop"))).snapshot.state == "stopped"
    assert all(item.config_key != "A" for item in runtime.service_snapshots)


@pytest.mark.anyio
async def test_stale_instance_workspace_and_unknown_target_cannot_expand_scope(control_runtime) -> None:
    runtime, config = control_runtime
    await runtime.control_service(request(runtime, "B", "start"))
    stale = McpServiceControlRequest("old-instance", str(runtime.workspace), "stop", McpSingleService("B"))
    assert (await runtime.control_service(stale)).outcome == "failed"
    assert (await runtime.control_service(request(runtime, "unknown", "stop"))).outcome == "failed"
    old = request(runtime, "B", "stop")
    config.workspace = config.workspace / "next"
    assert (await runtime.control_service(old)).outcome == "failed"
    assert runtime.started


@pytest.mark.anyio
async def test_prefix_collision_with_removed_live_connection_does_not_replace_routes(control_runtime) -> None:
    runtime, config = control_runtime
    await runtime.control_service(request(runtime, "Docs API", "force"))
    before = await ping(runtime, "mcp__docs-api__ping")
    del config.servers["Docs API"]
    result = await runtime.control_service(request(runtime, "Docs/API", "force"))
    assert result.outcome == "failed"
    assert (await ping(runtime, "mcp__docs-api__ping")).instance_id == before.instance_id
    await runtime.control_service(request(runtime, "Docs API", "stop"))
    assert (await runtime.control_service(request(runtime, "Docs/API", "force"))).outcome == "applied"


@pytest.mark.anyio
async def test_parallel_start_is_idempotent_and_cancelled_start_releases_owner(control_runtime) -> None:
    runtime, config = control_runtime
    first, second = await asyncio.gather(*(runtime.control_service(request(runtime, "A", "start")) for _ in range(2)))
    assert (first.outcome, second.outcome) == ("applied", "unchanged")
    assert sum(fact.event == "initialized" for fact in read_facts(config.workspace / "a.jsonl")) == 1
    task = asyncio.create_task(runtime.control_service(request(runtime, "Slow", "force")))
    await wait_for_fact(config.workspace / "slow.jsonl", "fault.injected")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await runtime.control_service(request(runtime, "Slow", "stop"))).snapshot.state == "stopped"
    assert (await ping(runtime, "mcp__a__ping")).call_count == 1


@pytest.mark.anyio
async def test_real_close_stall_is_reclaimed_before_restart(control_runtime) -> None:
    runtime, _ = control_runtime
    await runtime.control_service(request(runtime, "CloseStall", "force"))
    async with asyncio.timeout(15):
        result = await runtime.control_service(request(runtime, "CloseStall", "stop"))
    assert result.outcome == "applied" and result.snapshot.state == "stopped"


@pytest.mark.anyio
async def test_cancelled_stop_reports_confirmed_stopped_after_cleanup(control_runtime) -> None:
    runtime, config = control_runtime
    await runtime.control_service(request(runtime, "CloseStall", "force"))
    task = asyncio.create_task(runtime.control_service(request(runtime, "CloseStall", "stop")))
    await wait_for_fact(config.workspace / "close-stall.jsonl", "fault.injected")
    task.cancel()
    async with asyncio.timeout(10):
        result = await task
    assert result.outcome == "applied" and result.snapshot.state == "stopped"
    assert (await runtime.control_service(request(runtime, "CloseStall", "stop"))).outcome == "unchanged"


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
async def test_runtime_remote_stop_leaves_server_alive_and_restart_gets_new_session(control_runtime, transport: FixtureTransport) -> None:
    runtime, config = control_runtime
    async with remote_fixture(FixtureSpec(config.workspace, "remote", transport, repository=config.repository)) as remote:
        config.servers["remote"] = {"url": remote.url}
        try:
            await runtime.control_service(request(runtime, "remote", "start"))
            first = await ping(runtime, "mcp__remote__ping")
            await runtime.control_service(request(runtime, "remote", "stop"))
            assert remote.process.returncode is None
            await runtime.control_service(request(runtime, "remote", "restart"))
            second = await ping(runtime, "mcp__remote__ping")
            assert second.instance_id == first.instance_id
            assert second.session_id != first.session_id
        finally:
            await runtime.control_service(request(runtime, "remote", "stop"))


@pytest.mark.anyio
async def test_real_eof_updates_state_without_status_probe(control_runtime) -> None:
    runtime, config = control_runtime
    async with remote_fixture(FixtureSpec(config.workspace, "remote", "streamable_http", repository=config.repository)) as remote:
        config.servers["remote"] = {"url": remote.url}
        await runtime.control_service(request(runtime, "remote", "start"))
        remote.process.terminate()
        await remote.process.wait()
        async with asyncio.timeout(10):
            while next(item for item in runtime.service_snapshots if item.config_key == "remote").state == "ready":
                await asyncio.sleep(0.025)
        result = await runtime.control_service(request(runtime, "remote", "status"))
        assert result.snapshot.state == "failed" and not result.snapshot.tools


@pytest.mark.anyio
async def test_owner_output_matches_p0_contract_and_detached_owner_rejects_request(control_runtime, fixtures_root) -> None:
    runtime, config = control_runtime
    original = config.servers
    config.servers = {}
    owner = McpRuntimeOwner(runtime_factory=lambda: runtime)
    await owner.start()
    config.servers = original
    command = request(runtime, "all", "force")
    result = await owner.control_service(command)
    schema = json.loads((fixtures_root / "mcp" / "control.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema).evolve(schema={"$ref": "#/$defs/McpControlResult"})
    validator.validate(json.loads(json.dumps({"request": asdict(command), "services": [asdict(result)]})))
    assert result.snapshot.config_key == "all" and result.snapshot.state == "ready"
    assert owner.detach() is runtime
    assert (await owner.control_service(command)).outcome == "failed"


@pytest.mark.anyio
async def test_runtime_stdio_disconnect_is_failed_and_call_is_not_replayed(control_runtime) -> None:
    runtime, config = control_runtime
    await runtime.control_service(request(runtime, "B", "start"))
    await runtime.control_service(request(runtime, "Disconnect", "force"))
    async with asyncio.timeout(10):
        with pytest.raises(McpError):
            await ping(runtime, "mcp__disconnect__ping")
        while next(item for item in runtime.service_snapshots if item.config_key == "Disconnect").state == "ready":
            await asyncio.sleep(0.025)
    snapshot = (await runtime.control_service(request(runtime, "Disconnect", "status"))).snapshot
    assert snapshot.state == "failed" and snapshot.tools == ()
    assert sum(fact.event == "tool.started" for fact in read_facts(config.workspace / "disconnect.jsonl")) == 1
    assert (await ping(runtime, "mcp__b__ping")).call_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("case_id", [
    "single-force-is-idempotent", "single-stop-keeps-other-service",
    "restart-respects-disabled-configuration", "status-has-no-network-effects",
])
async def test_p0_golden_p1_cases_replay_against_real_runtime(control_runtime, fixtures_root, case_id) -> None:
    runtime, config = control_runtime
    cases = json.loads((fixtures_root / "mcp" / "control_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in cases if item["id"] == case_id)
    before: dict[str, FixtureReply] = {}
    for entry in case["initial"]:
        key = entry["config_key"]
        config.servers[key]["enabled"] = entry["enabled"]
        if entry["state"] == "ready":
            result = await runtime.control_service(request(runtime, key, "force"))
            before[key] = await ping(runtime, result.snapshot.tool_prefix + "ping")
    if case["request"]["target"]["scope"] == "single":
        result = await runtime.control_service(request(runtime, case["request"]["target"]["config_key"], case["request"]["action"]))
        assert result.outcome in ("applied", "unchanged", "disabled")
    snapshots = {item.config_key: item for item in runtime.service_snapshots}
    for key in case["expected"]["disconnect"]:
        assert snapshots[key].state == "stopped" and not snapshots[key].tools
    for key in case["expected"]["preserve"]:
        after = await ping(runtime, snapshots[key].tool_prefix + "ping")
        assert (after.instance_id, after.session_id) == (before[key].instance_id, before[key].session_id)


@pytest.mark.anyio
async def test_owner_retries_final_cleanup_after_failure() -> None:
    runtime = AsyncMock()
    runtime.stop.side_effect = [RuntimeError("cleanup failed"), None]
    owner = McpRuntimeOwner(runtime_factory=lambda: runtime)
    await owner.start()
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await owner.close()
    assert owner.current is None
    await owner.close()
    assert runtime.stop.await_count == 2


@pytest.mark.anyio
async def test_initial_start_failure_remains_visible_and_single_service_can_retry(control_runtime) -> None:
    runtime, config = control_runtime
    config.servers = {"A": config.servers["F"]}
    config.servers["A"]["enabled"] = True
    await runtime.start()
    assert not runtime.started
    assert runtime.service_snapshots[0].state == "failed"
    arguments = config.servers["A"]["args"]
    assert isinstance(arguments, list)
    config.servers["A"]["args"] = ["ready" if value == "startup-failure" else value for value in arguments]
    result = await runtime.control_service(request(runtime, "A", "start"))
    assert result.outcome == "applied" and result.snapshot.state == "ready"
