# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

from agent.application.config.settings import FeatureSettings
from agent.application.tools.coding import coding_tools
from agent.application.tools.javascript import JS_REPL_TOOL_NAMES
from agent.application.tools.media import media_tools
from agent.application.tools.permissions import permission_tools
from agent.application.tools.planning import planning_tools
from agent.application.tools.plan_update import update_plan_tools
from agent.application.tools.subagents import subagent_tools
from agent.ports.approvals import ApprovalCoordinatorPort
from agent.ports.media import ImageReaderPort
from agent.ports.permissions import PermissionGrantPort
from agent.ports.subagents import SubagentControlPort
from agent.ports.workspace import (
    ExecutionPolicy,
    WorkspaceCodingPort,
)
from infrastructure.mcp.local_tool_registry import ToolRegistry


def build_client_tool_registry(
    coding: WorkspaceCodingPort,
    *,
    image_reader: ImageReaderPort,
    execution_policy: ExecutionPolicy | None = None,
    subagent_runtime: SubagentControlPort | None = None,
    approval_coordinator: ApprovalCoordinatorPort | None = None,
    features: FeatureSettings | None = None,
) -> ToolRegistry:
    """构建经过 MCP SDK 适配的默认客户端工具注册表。"""
    feature_settings = features or FeatureSettings()
    coding_definitions = coding_tools(
        coding,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        exec_permission_approvals_enabled=(
            feature_settings.exec_permission_approvals
        ),
    )
    if not feature_settings.js_repl:
        coding_definitions = [
            tool
            for tool in coding_definitions
            if tool.name not in JS_REPL_TOOL_NAMES
        ]

    tools = [
        *planning_tools(),
        *update_plan_tools(),
        *coding_definitions,
        *media_tools(image_reader),
    ]
    if subagent_runtime is not None and subagent_runtime.enabled:
        tools.extend(subagent_tools(subagent_runtime))
    return ToolRegistry(tools)


def build_builtin_tool_registry(
    *,
    approval_coordinator: ApprovalCoordinatorPort | None,
    permission_grants: PermissionGrantPort,
    features: FeatureSettings,
) -> ToolRegistry:
    """构建经过 MCP SDK 适配的 Harness 内置工具注册表。"""
    tools = (
        permission_tools(approval_coordinator, permission_grants)
        if features.request_permissions_tool
        else ()
    )
    return ToolRegistry(tools)


__all__ = (
    "build_builtin_tool_registry",
    "build_client_tool_registry",
)

if __name__ == "__main__":
    pass
