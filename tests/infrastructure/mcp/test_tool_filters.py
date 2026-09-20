"""验证 Codex 工具过滤配置从 TOML 边界到 MCP 目录的语义。"""

import tomllib

import pytest
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    Mock,
)

from mcp import (
    ClientSession,
    types as mcp_types,
)

from agent.protocol.json_value import ThawedJsonValue
from infrastructure.config.schema import (
    ConfigValidationError,
    config_override,
    validate_config,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.settings import (
    is_mcp_tool_allowed,
    normalize_mcp_servers,
)


@pytest.mark.anyio
@pytest.mark.parametrize(("fields", "expected"), [
    ("", ["read", "list", "write"]),
    ('enabled_tools = ["read", "list"]', ["read", "list"]),
    ('disabled_tools = ["write"]', ["read", "list"]),
    ('enabled_tools = ["read", "list"]\ndisabled_tools = ["read"]', ["list"]),
    ('enabled_tools = []', []),
    ('disabled_tools = []', ["read", "list", "write"]),
    ('enabled_tools = []\ndisabled_tools = []', []),
    ('enabled_tools = ["r*"]', []),
    ('enabled_tools = ["READ"]', []),
    ('enabled_tools = [" read "]', []),
    ('enabled_tools = ["mcp__docs__read"]', []),
    ('enabled_tools = ["unknown", ""]', []),
    ('enabled_tools = ["read", "read"]', ["read"]),
    ('disabled_tools = ["*"]', ["read", "list", "write"]),
    ('disabled_tools = ["READ", " read ", "mcp__docs__read", "unknown"]', ["read", "list", "write"]),
])
async def test_toml_filters_apply_before_tool_registration(
    tmp_path: Path, fields: str, expected: list[str],
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[mcp_servers.docs]\ncommand = "fixture"\n' + fields, encoding="utf-8")
    config = ConfigSession(ConfigStore(path), workspace=tmp_path).load()
    server = normalize_mcp_servers(config["mcp_servers"], environment={})[0]
    session = Mock(spec=ClientSession)
    session.get_server_capabilities.return_value = mcp_types.ServerCapabilities(tools=mcp_types.ToolsCapability())
    session.list_tools = AsyncMock(return_value=mcp_types.ListToolsResult(tools=[
        mcp_types.Tool(name=name, inputSchema={}) for name in ("read", "list", "write")
    ]))
    tools, discovered = await ExternalMcpGroup._collect_tools(
        mcp_types.Implementation(name="docs", version="1"), session,
        rules=server["tool_filter"],
    )
    assert discovered == 3
    assert list(tools) == [f"mcp__docs__{name}" for name in expected]
    assert [tool.name for tool in tools.values()] == expected


@pytest.mark.parametrize("name", ["READ", " read ", "r*", "r?", "[read]"])
def test_tool_names_are_literal_in_both_lists(name: str) -> None:
    assert is_mcp_tool_allowed(name, {"enabled_tools": [name]})
    assert not is_mcp_tool_allowed("read", {"enabled_tools": [name]})
    assert not is_mcp_tool_allowed(name, {"disabled_tools": [name]})
    assert is_mcp_tool_allowed("read", {"disabled_tools": [name]})


@pytest.mark.parametrize("field", ["enabled_tools", "disabled_tools"])
@pytest.mark.parametrize("value", ['"read"', '[1]', '[true]', '{}'])
def test_filter_types_are_validated_in_files_and_overrides(field: str, value: str) -> None:
    data = tomllib.loads(f'[mcp_servers.docs]\ncommand = "fixture"\n{field} = {value}')
    with pytest.raises(ConfigValidationError, match="array of strings"):
        validate_config(data)
    with pytest.raises(ConfigValidationError, match="array of strings"):
        config_override(("mcp_servers", "docs", field), tomllib.loads(f"value = {value}")["value"])


@pytest.mark.parametrize("field", ["allow", "deny"])
def test_removed_filter_fields_fail_at_config_boundary(field: str) -> None:
    with pytest.raises(ConfigValidationError, match="unknown MCP server key"):
        validate_config({"mcp_servers": {"docs": {"command": "fixture", field: ["read"]}}})
    with pytest.raises(ConfigValidationError, match="unknown MCP server key"):
        config_override(("mcp_servers", "docs", field), ["read"])


@pytest.mark.parametrize("enabled_tools", [[], ["list"]])
def test_profile_overrides_replace_enabled_list(
    tmp_path: Path, enabled_tools: list[str],
) -> None:
    store = ConfigStore(tmp_path / "config.toml")
    store.update({("mcp_servers", "docs"): {
        "command": "fixture", "enabled_tools": ["read"], "disabled_tools": ["write"],
    }})
    override: dict[str, ThawedJsonValue] = {"enabled_tools": list(enabled_tools)}
    ConfigStore(tmp_path / "work.config.toml").update({("mcp_servers", "docs"): override})
    config = ConfigSession(store, profile="work", workspace=tmp_path).load()
    server = normalize_mcp_servers(config["mcp_servers"], environment={})[0]
    assert server["tool_filter"] == {"enabled_tools": enabled_tools, "disabled_tools": ["write"]}
