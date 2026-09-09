# -*- coding: utf-8 -*-

from unittest.mock import AsyncMock

import pytest

from agent.application.approvals.mcp import authorize_mcp_tool_call
from agent.application.approvals.models import ApprovalOutcome
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.domain.approvals import (
    ActionFingerprint,
    McpApprovalMode,
    McpApprovalPolicy,
    McpToolAnnotations,
    McpToolDescriptor,
)
from agent.domain.policies import PermissionSettings


class _Coordinator:
    """记录是否进入人机审批，并返回预设决定。"""

    def __init__(self, decision: str) -> None:
        self.request_action_outcome = AsyncMock(
            return_value=ApprovalOutcome.create(
                decision,
                source="user",
                reason="user",
            ),
        )


def _descriptor(
    mode: McpApprovalMode,
    *,
    read_only: bool | None,
    destructive: bool | None,
    open_world: bool | None,
) -> McpToolDescriptor:
    """构造一个固定身份的 MCP 工具描述。"""
    return McpToolDescriptor(
        server="matrix",
        exposed_name="mcp__matrix__tool",
        tool_name="tool",
        schema_fingerprint=ActionFingerprint("schema:matrix:tool"),
        annotations=McpToolAnnotations(
            read_only_hint=read_only,
            destructive_hint=destructive,
            open_world_hint=open_world,
        ),
        policy=McpApprovalPolicy(mode),
        title="Matrix tool",
        connector_id="matrix",
        transport="stdio",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "sandbox_mode",
        "approval_policy",
        "mode",
        "read_only",
        "destructive",
        "open_world",
        "decision",
        "expected_allowed",
        "expected_requests",
    ),
    [
        ("danger-full-access", "never", McpApprovalMode.PROMPT, None, None, None, "decline", True, 0),
        ("read-only", "never", McpApprovalMode.PROMPT, None, None, None, "accept", False, 0),
        ("workspace-write", "on-request", McpApprovalMode.PROMPT, None, None, None, "accept", True, 1),
        ("workspace-write", "on-request", McpApprovalMode.PROMPT, None, None, None, "decline", False, 1),
        ("workspace-write", "on-request", McpApprovalMode.APPROVE, None, None, None, "decline", True, 0),
        ("workspace-write", "on-request", McpApprovalMode.WRITES, True, None, None, "decline", True, 0),
        ("workspace-write", "on-request", McpApprovalMode.WRITES, None, None, None, "accept", True, 1),
        ("workspace-write", "on-request", McpApprovalMode.AUTO, None, False, False, "decline", True, 0),
        ("workspace-write", "on-request", McpApprovalMode.AUTO, None, False, None, "accept", True, 1),
    ],
)
async def test_mcp_authorization_matrix_controls_approval_and_execution_gate(
    sandbox_mode: str,
    approval_policy: str,
    mode: McpApprovalMode,
    read_only: bool | None,
    destructive: bool | None,
    open_world: bool | None,
    decision: str,
    expected_allowed: bool,
    expected_requests: int,
) -> None:
    """验证全局权限、工具模式和用户决定的组合边界。"""
    coordinator = _Coordinator(decision)
    turn = TurnContext.create(
        agent=AgentContext.root("sid-matrix"),
        cid="cid-matrix",
        sid="sid-matrix",
        source="test",
        pref_config={},
        cwd="D:/workspace",
        permissions=PermissionSettings(
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
        ),
        turn_id="turn-matrix",
    )

    result = await authorize_mcp_tool_call(
        turn,
        coordinator=coordinator,
        call_id="call-matrix",
        descriptor=_descriptor(
            mode,
            read_only=read_only,
            destructive=destructive,
            open_world=open_world,
        ),
        arguments={"query": "matrix"},
    )

    assert result.allowed is expected_allowed
    assert coordinator.request_action_outcome.await_count == expected_requests
    if expected_requests:
        assert result.presentation is not None
        assert result.presentation["kind"] == "mcp_tool_call"
