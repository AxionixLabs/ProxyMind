"""校验 P0 验收值的作用范围和状态边界，不宣称运行时已实现新控制语义。"""

import json

import pytest

from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from infrastructure.mcp.settings import normalize_mcp_servers


@pytest.fixture
def contract(fixtures_root: Path) -> Draft202012Validator:
    schema = json.loads((fixtures_root / "mcp" / "control.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def request(target: JsonValue, action: str = "status") -> dict[str, JsonValue]:
    """构造带运行实例和工作区身份的验收请求。"""
    return {"runtime_id": "instance-a", "workspace": "/workspace/a", "target": target, "action": action}


def snapshot(**updates: JsonValue) -> dict[str, JsonValue]:
    """构造有效零工具连接，供各个状态边界用例修改。"""
    return {
        "config_key": "A", "tool_prefix": "mcp__a__", "config_enabled": True,
        "state": "ready", "transport": "stdio", "tools": [], "discovered": 0,
        "filtered": 0, "connection_error": None, **updates,
    }


def validates(contract: Draft202012Validator, name: str, value: JsonValue) -> bool:
    """按具名验收定义校验输入，不调用未来的运行时接口。"""
    return contract.evolve(schema={"$ref": f"#/$defs/{name}"}).is_valid(value)


@pytest.mark.parametrize("action", ["start", "force", "stop", "restart", "status"])
def test_ac01_menu_target_is_single_and_cancellation_cannot_mean_all(contract, action) -> None:
    assert validates(contract, "McpMenuSelection", None)
    assert validates(contract, "McpMenuSelection", request({"scope": "single", "config_key": "all"}, action))
    assert not validates(contract, "McpMenuSelection", request({"scope": "all"}, action))
    assert validates(contract, "McpControlRequest", request({"scope": "all"}, action))
    assert not validates(contract, "McpControlRequest", None)


@pytest.mark.parametrize("target", [None, "all", "", {}, {"scope": "single"}, {"scope": "single", "config_key": " "}, {"scope": "all", "config_key": "A"}])
def test_ac02_ambiguous_target_is_not_an_operation(contract, target) -> None:
    assert not validates(contract, "McpControlRequest", request(target))


def test_ac01_server_menu_has_no_all_entry_and_accepts_empty_configuration(contract) -> None:
    assert validates(contract, "McpServerMenu", {"services": []})
    assert validates(contract, "McpServerMenu", {"services": [{"scope": "single", "config_key": "all"}]})
    assert not validates(contract, "McpServerMenu", {"services": [{"scope": "all"}]})


def test_ac02_action_order_and_default_status_are_fixed(contract) -> None:
    value: dict[str, JsonValue] = {
        "target": {"scope": "single", "config_key": "A"},
        "actions": ["start", "force", "stop", "restart", "status"], "selected": "status",
    }
    assert validates(contract, "McpServiceMenu", value)
    value["selected"] = "stop"
    assert not validates(contract, "McpServiceMenu", value)
    value["selected"] = "status"
    value["actions"] = ["status", "start", "force", "stop", "restart"]
    assert not validates(contract, "McpServiceMenu", value)


@pytest.mark.parametrize("action", ["force stop", "stop extra", "unknown", "", "START"])
def test_ac02_contract_rejects_unparsed_or_unknown_action(contract, action) -> None:
    assert not validates(contract, "McpControlRequest", request({"scope": "all"}, action))


@pytest.mark.parametrize("field", ["runtime_id", "workspace", "target", "action"])
def test_request_requires_frozen_context_and_explicit_target(contract, field) -> None:
    value = request({"scope": "single", "config_key": "A"})
    del value[field]
    assert not validates(contract, "McpControlRequest", value)


@pytest.mark.parametrize("enabled", [True, False, None])
def test_ac07_empty_tools_do_not_replace_configuration_or_connection_state(contract, enabled) -> None:
    assert validates(contract, "McpServiceSnapshot", snapshot(config_enabled=enabled))
    assert validates(contract, "McpServiceSnapshot", snapshot(config_enabled=enabled, discovered=2, filtered=2))
    assert validates(contract, "McpServiceSnapshot", snapshot(config_enabled=enabled, state="stopped"))
    assert validates(contract, "McpServiceSnapshot", snapshot(config_enabled=enabled, state="failed", connection_error="discovery failed"))


@pytest.mark.parametrize("updates", [
    {"state": "disabled"}, {"state": "failed"},
    {"state": "failed", "connection_error": " "},
    {"state": "stopped", "tools": ["mcp__a__ping"]},
    {"discovered": -1}, {"filtered": True},
    {"tools": ["mcp__a__ping", "mcp__a__ping"]},
])
def test_ac07_invalid_or_unavailable_tool_snapshot_is_rejected(contract, updates) -> None:
    assert not validates(contract, "McpServiceSnapshot", snapshot(**updates))


def test_busy_result_preserves_ready_connection_and_single_result_scope(contract) -> None:
    outcome: dict[str, JsonValue] = {
        "config_key": "A", "outcome": "busy", "snapshot": snapshot(),
        "operation_error": "active tool snapshot",
    }
    result: dict[str, JsonValue] = {
        "request": request({"scope": "single", "config_key": "A"}, "stop"),
        "services": [outcome],
    }
    assert validates(contract, "McpControlResult", result)
    result["services"] = [outcome, outcome]
    assert not validates(contract, "McpControlResult", result)


def test_normalized_tool_alias_is_not_configuration_identity() -> None:
    config = {
        "Docs API": {"command": "fixture"},
        "Docs/API": {"command": "fixture"},
        "all": {"command": "fixture", "enabled": False},
    }
    normalized = normalize_mcp_servers(config)
    assert [server["config_key"] for server in normalized] == list(config)
    assert len({server["name"] for server in normalized}) == 3
    selected = next(server for server in normalized if server["config_key"] == "Docs/API")
    after_delete = normalize_mcp_servers({"Docs API": config["Docs API"]})
    assert all(server["config_key"] != selected["config_key"] for server in after_delete)
    assert normalize_mcp_servers({"Docs/API": config["Docs/API"]})[0]["name"] != selected["name"]


def test_golden_lifecycle_inputs_use_the_named_contract(contract, fixtures_root: Path) -> None:
    data = json.loads((fixtures_root / "mcp" / "control_cases.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict) and isinstance(data.get("cases"), list)
    case_ids: set[str] = set()
    for case in data["cases"]:
        assert isinstance(case, dict)
        assert isinstance(case["id"], str) and case["id"] not in case_ids
        case_ids.add(case["id"])
        assert validates(contract, "McpControlRequest", case["request"])
        assert isinstance(case["initial"], list)
        initial_keys = {item["config_key"] for item in case["initial"]}
        assert len(initial_keys) == len(case["initial"])
        for entry in case["initial"]:
            assert validates(contract, "McpSingleService", {"scope": "single", "config_key": entry["config_key"]})
            assert validates(contract, "McpConnectionState", entry["state"])
            assert isinstance(entry["enabled"], bool)
        assert isinstance(case["expected"], dict)
        for keys in case["expected"].values():
            assert isinstance(keys, list)
            assert set(keys) <= initial_keys
        assert not set(case["expected"]["disconnect"]) & set(case["expected"]["preserve"])
