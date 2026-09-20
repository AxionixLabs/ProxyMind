# -*- coding: utf-8 -*-

import json
import pytest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from frontends.cli import mcp_registry
from frontends.cli.commands import (
    McpAddCommand,
    McpGetCommand,
    McpLoginCommand,
    McpLogoutCommand,
)
from frontends.cli.entry import run
from frontends.cli.parser import parse_cli_command
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.settings import normalize_mcp_servers


def test_mcp_add_parses_runtime_policy_fields() -> None:
    command = parse_cli_command([
        "mcp",
        "add",
        "browser",
        "--url",
        "https://example.test/mcp",
        "--enabled-tool",
        "browser_navigate",
        "--enabled-tool",
        "browser_snapshot",
        "--disabled-tool",
        "browser_evaluate",
        "--required",
        "--approval-mode",
        "writes",
        "--startup-timeout-sec",
        "12",
        "--tool-timeout-sec",
        "45",
    ])

    assert command == McpAddCommand(
        name="browser",
        url="https://example.test/mcp",
        required=True,
        enabled_tools=("browser_navigate", "browser_snapshot"),
        disabled_tools=("browser_evaluate",),
        approval_mode="writes",
        startup_timeout_sec=12.0,
        tool_timeout_sec=45.0,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["stdio", "http"])
async def test_mcp_add_round_trips_exact_tool_names(
    tmp_path: Path, transport: str,
) -> None:
    config_path = tmp_path / "config.toml"
    target = ["--", "fixture"] if transport == "stdio" else [
        "--url", "https://example.test/mcp", "--bearer-token-env-var", "MCP_FILTER_TEST_TOKEN",
    ]
    command = parse_cli_command([
        "mcp", "add", "docs",
        "--enabled-tool", "read", "--enabled-tool", " read ",
        "--enabled-tool", "read_*", "--disabled-tool", "write",
        "--disabled-tool", "Read", *target,
    ])
    assert isinstance(command, McpAddCommand)
    output = StringIO()
    with patch.object(mcp_registry, "application_config_path", return_value=config_path):
        assert await mcp_registry.run_mcp_registry_command(command, output_stream=StringIO()) == 0
        assert await mcp_registry.run_mcp_registry_command(
            McpGetCommand("docs", output_format="json"), output_stream=output,
        ) == 0
    config = json.loads(output.getvalue())["config"]
    expected = {
        "enabled_tools": ["read", " read ", "read_*"],
        "disabled_tools": ["write", "Read"],
    }
    assert config == {**expected, "enabled": True, **(
        {"command": "fixture", "args": []} if transport == "stdio" else {
            "url": "https://example.test/mcp", "bearer_token_env_var": "MCP_FILTER_TEST_TOKEN",
        }
    )}
    assert normalize_mcp_servers(ConfigStore(config_path).read_raw()["mcp_servers"])[0]["tool_filter"] == expected


@pytest.mark.anyio
@pytest.mark.parametrize("names", [None, ()])
async def test_mcp_add_preserves_unset_and_empty_enabled_tools(
    tmp_path: Path, names: tuple[str, ...] | None,
) -> None:
    config_path = tmp_path / "config.toml"
    command = McpAddCommand("docs", stdio_command=("fixture",), enabled_tools=names)
    with patch.object(mcp_registry, "application_config_path", return_value=config_path):
        assert await mcp_registry.run_mcp_registry_command(command, output_stream=StringIO()) == 0
    server = normalize_mcp_servers(ConfigStore(config_path).read_raw()["mcp_servers"])[0]
    assert server["tool_filter"] == ({} if names is None else {"enabled_tools": []})


@pytest.mark.parametrize("option", ["--allow", "--deny"])
def test_mcp_add_rejects_removed_filter_options(option: str) -> None:
    with pytest.raises(SystemExit) as result:
        parse_cli_command(["mcp", "add", "docs", "--url", "https://example.test/mcp", option, "read"])
    assert result.value.code == 2


@pytest.mark.anyio
async def test_mcp_output_redacts_inline_credentials(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    ConfigStore(config_path).update({
        ("mcp_servers", "remote"): {
            "url": (
                "https://example.test/server/"
                "0123456789abcdef0123456789abcdef?key=secret#token"
            ),
            "http_headers": {"Authorization": "secret"},
        },
    })
    monkeypatch.setattr(
        mcp_registry,
        "application_config_path",
        lambda: config_path,
    )
    output = StringIO()

    result = await mcp_registry.run_mcp_registry_command(
        McpGetCommand("remote"),
        output_stream=output,
    )

    assert result == 0
    assert "secret" not in output.getvalue()
    assert "0123456789abcdef" not in output.getvalue()
    assert "<redacted>" in output.getvalue()


def test_login_and_logout_parse_raw_key_and_scope_override() -> None:
    assert parse_cli_command(["mcp", "login", " raw ", "--scopes", "read,write,read", "--timeout-sec", "12"]) == McpLoginCommand(" raw ", ("read", "write"), 12)
    assert parse_cli_command(["mcp", "login", " raw ", "--scopes", ""]) == McpLoginCommand(" raw ", ())
    assert parse_cli_command(["mcp", "login", " raw "]) == McpLoginCommand(" raw ")
    assert parse_cli_command(["mcp", "login", " raw ", "--manual"]) == McpLoginCommand(" raw ", manual=True)
    assert parse_cli_command(["mcp", "logout", " raw "]) == McpLogoutCommand(" raw ")


@pytest.mark.parametrize("arguments", [
    ["login"], ["logout"], ["login", " "],
    ["login", "server", "--timeout-sec", "nan"],
    ["login", "server", "--timeout-sec", "0"],
    ["login", "server", "--scopes", "read,,write"],
    ["login", "server", "--scopes", "two scopes"],
])
def test_oauth_syntax_errors_return_two(arguments: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_result:
        run(arguments=["mcp", *arguments])
    assert exit_result.value.code == 2
    assert capsys.readouterr().err


@pytest.mark.parametrize("command", ["login", "logout"])
def test_oauth_command_help(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_result:
        run(arguments=["mcp", command, "--help"])
    assert exit_result.value.code == 0
    help_text = capsys.readouterr().out
    assert command in help_text and "NAME" in help_text
    if command == "login":
        assert "--scopes" in help_text and "--timeout-sec" in help_text and "--manual" in help_text
