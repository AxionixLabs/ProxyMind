# -*- coding: utf-8 -*-

import pytest

from agent.domain.approvals import (
    ActionFingerprint,
    McpApprovalMode,
    McpApprovalPolicy,
    McpToolAnnotations,
    McpToolDescriptor,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.approval_policy import ConfigMcpPersistentApprovalStore
from infrastructure.mcp.settings import McpConfigError


def _descriptor(config_server_key: str | None = "Docs API") -> McpToolDescriptor:
    return McpToolDescriptor(
        server="docs-api",
        exposed_name="mcp__docs-api__publish",
        tool_name="publish",
        schema_fingerprint=ActionFingerprint("schema"),
        annotations=McpToolAnnotations(read_only_hint=False),
        policy=McpApprovalPolicy(McpApprovalMode.PROMPT),
        config_server_key=config_server_key,
    )


@pytest.mark.anyio
async def test_persistent_mcp_approval_atomically_updates_exact_config_server(
    tmp_path,
) -> None:
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    config.update_user({
        ("mcp_servers", "Docs API"): {
            "command": "docs-server",
            "default_tools_approval_mode": "prompt",
        },
    })
    approvals = ConfigMcpPersistentApprovalStore(config)

    await approvals.approve_tool(_descriptor())

    raw = config.store.read_raw()
    assert raw["mcp_servers"]["Docs API"]["tools"]["publish"] == {
        "approval_mode": "approve",
    }
    assert "docs-api" not in raw["mcp_servers"]


@pytest.mark.anyio
async def test_persistent_mcp_approval_rejects_missing_config_identity(
    tmp_path,
) -> None:
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"))
    config.store.ensure()
    approvals = ConfigMcpPersistentApprovalStore(config)

    with pytest.raises(McpConfigError, match="identity"):
        await approvals.approve_tool(_descriptor(None))

    assert config.store.read_raw()["mcp_servers"] == {}
