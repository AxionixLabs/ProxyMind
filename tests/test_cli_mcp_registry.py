# -*- coding: utf-8 -*-

from io import StringIO

from frontends.cli import mcp_registry
from frontends.cli.commands import McpAddCommand, McpGetCommand
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
        startup_timeout_sec=12.0,
        tool_timeout_sec=45.0,
    )


def test_mcp_output_redacts_inline_credentials(monkeypatch, tmp_path) -> None:
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
        "mind_config_path",
        lambda: config_path,
    )
    output = StringIO()

    result = mcp_registry.run_mcp_registry_command(
        McpGetCommand("remote"),
        output_stream=output,
    )

    assert result == 0
    assert "secret" not in output.getvalue()
    assert "0123456789abcdef" not in output.getvalue()
    assert "<redacted>" in output.getvalue()
