# -*- coding: utf-8 -*-

import pytest
from io import StringIO

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


def test_mcp_add_parses_runtime_policy_fields() -> None:
    command = parse_cli_command([
        "mcp",
        "add",
        "browser",
        "--url",
        "https://example.test/mcp",
        "--allow",
        "browser_*",
        "--deny",
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
        allow=("browser_*",),
        deny=("browser_evaluate",),
        approval_mode="writes",
        startup_timeout_sec=12.0,
        tool_timeout_sec=45.0,
    )


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
