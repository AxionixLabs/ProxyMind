# -*- coding: utf-8 -*-

import pytest
from mcp import types as mcp_types
from mcp.client.session_group import SseServerParameters
from mcp.client.stdio import StdioServerParameters

from infrastructure.config.schema import (
    ConfigValidationError,
    validate_config,
)
from infrastructure.mcp.settings import normalize_mcp_servers
from infrastructure.mcp.transport import build_server_params
from infrastructure.mcp.values import (
    tool_name_hook,
    truncate_text,
)


def test_mcp_settings_normalize_stdio_and_remote_servers() -> None:
    servers = normalize_mcp_servers({
        "Local Shell": {
            "command": "runner",
            "args": ["serve", 2],
            "env": {"MODE": "test"},
            "cwd": ".",
            "required": True,
            "allow": ["read_*", "read_*", ""],
            "deny": ["read_secret"],
            "startup_timeout_sec": 12,
            "tool_timeout_sec": 30,
            "tools": {
                "create_issue": {"approval_mode": "prompt"},
            },
            "default_tools_approval_mode": "writes",
        },
        "Docs API": {
            "url": "https://docs.example.test/sse",
            "http_headers": {"X-Client": "proxy"},
        },
        "invalid": {
            "command": "runner",
            "url": "https://invalid.example.test/mcp",
        },
    })

    assert servers == [
        {
            "name": "local-shell",
            "enabled": True,
            "required": True,
            "transport": "stdio",
            "startup_timeout_sec": 12.0,
            "timeout_sec": 30.0,
            "tool_filter": {
                "allow": ["read_*"],
                "deny": ["read_secret"],
            },
            "default_tools_approval_mode": "writes",
            "tool_approval_modes": {"create_issue": "prompt"},
            "command": "runner",
            "args": ["serve", "2"],
            "env": {"MODE": "test"},
            "cwd": ".",
            "encoding": "utf-8",
            "encoding_error_handler": "strict",
        },
        {
            "name": "docs-api",
            "enabled": True,
            "required": False,
            "transport": "sse",
            "startup_timeout_sec": 10.0,
            "timeout_sec": 60.0,
            "tool_filter": {},
            "default_tools_approval_mode": "auto",
            "tool_approval_modes": {},
            "url": "https://docs.example.test/sse",
            "headers": {"X-Client": "proxy"},
            "sse_read_timeout_sec": 60.0,
            "terminate_on_close": True,
        },
    ]


def test_mcp_approval_modes_are_validated_at_config_boundary() -> None:
    validate_config({
        "mcp_servers": {
            "docs": {
                "command": "runner",
                "default_tools_approval_mode": "writes",
                "tools": {
                    "publish": {"approval_mode": "prompt"},
                },
            },
        },
    })

    with pytest.raises(ConfigValidationError, match="approval_mode"):
        validate_config({
            "mcp_servers": {
                "docs": {
                    "command": "runner",
                    "tools": {
                        "publish": {"approval_mode": "sometimes"},
                    },
                },
            },
        })


def test_mcp_transport_builds_validated_sdk_parameters() -> None:
    stdio = build_server_params({
        "transport": "stdio",
        "command": "runner",
        "args": ["serve"],
        "env": {"MODE": "test"},
        "encoding_error_handler": "unsupported",
    })
    sse = build_server_params({
        "transport": "sse",
        "url": "https://docs.example.test/sse",
        "headers": {"X-Client": "proxy"},
        "timeout_sec": 12,
        "sse_read_timeout_sec": 30,
    })

    assert isinstance(stdio, StdioServerParameters)
    assert stdio.command == "runner"
    assert stdio.args == ["serve"]
    assert stdio.env == {"MODE": "test"}
    assert stdio.encoding_error_handler == "strict"

    assert isinstance(sse, SseServerParameters)
    assert sse.url == "https://docs.example.test/sse"
    assert sse.headers == {"X-Client": "proxy"}
    assert sse.timeout == 12.0
    assert sse.sse_read_timeout == 30.0


def test_mcp_values_keep_stable_names_and_bounded_text() -> None:
    server = mcp_types.Implementation(name="Docs API", version="1")

    assert tool_name_hook("search files", server) == (
        "mcp__docs-api__search_files"
    )
    assert truncate_text("abcdefgh", 6) == "abc..."
