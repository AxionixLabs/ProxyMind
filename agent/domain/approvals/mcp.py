# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

from .models import (
    McpApprovalMode,
    McpApprovalRisk,
    McpToolAnnotations,
    McpToolDescriptor,
)


def mcp_approval_risk(
    annotations: McpToolAnnotations,
) -> McpApprovalRisk:
    """按保守优先级把 MCP 注解归约为单一展示风险。"""
    if annotations.destructive_hint is True:
        return McpApprovalRisk.DESTRUCTIVE
    if annotations.open_world_hint is True:
        return McpApprovalRisk.OPEN_WORLD
    if annotations.read_only_hint is True:
        return McpApprovalRisk.READ_ONLY
    if (
        annotations.destructive_hint is False
        and annotations.open_world_hint is False
    ):
        return McpApprovalRisk.EXTERNAL_WRITE
    return McpApprovalRisk.UNKNOWN


def mcp_requires_approval(descriptor: McpToolDescriptor) -> bool:
    """按工具策略和可信注解判断一次 MCP 调用是否需要询问。"""
    mode = descriptor.policy.mode
    if mode is McpApprovalMode.APPROVE:
        return False
    if mode is McpApprovalMode.PROMPT:
        return True
    if mode is McpApprovalMode.WRITES:
        return descriptor.annotations.read_only_hint is not True

    annotations = descriptor.annotations
    if annotations.read_only_hint is True:
        return False
    return (
        annotations.destructive_hint is not False
        or annotations.open_world_hint is not False
    )


if __name__ == '__main__':
    pass
