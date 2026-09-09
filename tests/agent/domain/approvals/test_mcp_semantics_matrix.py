# -*- coding: utf-8 -*-

import itertools

import pytest

from agent.domain.approvals import (
    ActionFingerprint,
    McpApprovalMode,
    McpApprovalPolicy,
    McpToolAnnotations,
    McpToolDescriptor,
    mcp_approval_risk,
    mcp_requires_approval,
)


def _descriptor(
    mode: McpApprovalMode,
    *,
    read_only: bool | None,
    destructive: bool | None,
    open_world: bool | None,
) -> McpToolDescriptor:
    """构造一个用于语义矩阵的固定 MCP 工具描述。"""
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
    )


_ANNOTATIONS = tuple(itertools.product((None, False, True), repeat=3))


@pytest.mark.parametrize("read_only,destructive,open_world", _ANNOTATIONS)
@pytest.mark.parametrize("mode", tuple(McpApprovalMode))
def test_mcp_approval_modes_match_codex_conformance_matrix(
    mode: McpApprovalMode,
    read_only: bool | None,
    destructive: bool | None,
    open_world: bool | None,
) -> None:
    """逐项锁定 codex-main 的四种 MCP 工具审批模式和保守注解规则。"""
    descriptor = _descriptor(
        mode,
        read_only=read_only,
        destructive=destructive,
        open_world=open_world,
    )

    if mode is McpApprovalMode.APPROVE:
        expected = False
    elif mode is McpApprovalMode.PROMPT:
        expected = True
    elif mode is McpApprovalMode.WRITES:
        expected = read_only is not True
    elif read_only:
        expected = False
    else:
        expected = not (
            destructive is False and open_world is False
        )

    assert mcp_requires_approval(descriptor) is expected


@pytest.mark.parametrize(
    ("annotations", "expected"),
    [
        (McpToolAnnotations(read_only_hint=True, destructive_hint=True), "destructive"),
        (McpToolAnnotations(read_only_hint=True, open_world_hint=True), "open-world"),
        (McpToolAnnotations(read_only_hint=True), "read-only"),
        (McpToolAnnotations(destructive_hint=False, open_world_hint=False), "external write"),
        (McpToolAnnotations(), "unknown"),
    ],
)
def test_mcp_risk_projection_is_stable(
    annotations: McpToolAnnotations,
    expected: str,
) -> None:
    """风险展示按 destructive、open-world、read-only 的固定优先级收敛。"""
    assert mcp_approval_risk(annotations).value == expected
