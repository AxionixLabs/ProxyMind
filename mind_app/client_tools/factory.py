# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from agent.application.config.settings import FeatureSettings
from infrastructure.mcp.local_tool_registry import ToolRegistry
from mind_app.native_coding import NativeCoding
from .coding import coding_tools
from .planning import planning_tools
from .subagents import subagent_tools
from .update_plan import update_plan_tools
from .view_image import view_image_tools

if typing.TYPE_CHECKING:
    from agent.application.approvals.coordinator import ApprovalCoordinator
    from infrastructure.config.execution_policy_manager import ExecPolicyManager
    from agent.harness.agents.runtime import SubagentRuntime

JS_REPL_TOOL_NAMES = frozenset({"js_repl", "js_repl_reset"})


def default_registry(
    native_coding: NativeCoding,
    *,
    execution_root: str | Path | None = None,
    exec_policy_manager: "ExecPolicyManager | None" = None,
    subagent_runtime: "SubagentRuntime | None" = None,
    approval_coordinator: "ApprovalCoordinator | None" = None,
    features: FeatureSettings | None = None,
) -> ToolRegistry:
    """构建默认客户端工具注册表。"""
    feature_settings = features or FeatureSettings()
    root_source = execution_root
    if root_source is None:
        root_source = native_coding.root

    root = Path(root_source or Path.cwd()).resolve()

    coding = coding_tools(
        native_coding,
        approval_coordinator=approval_coordinator,
        exec_policy_manager=exec_policy_manager,
        exec_permission_approvals_enabled=feature_settings.exec_permission_approvals,
    )
    if not feature_settings.js_repl:
        coding = [
            tool
            for tool in coding
            if tool.name not in JS_REPL_TOOL_NAMES
        ]

    tools = [
        *planning_tools(),
        *update_plan_tools(),
        *coding,
        *view_image_tools(root),
    ]

    if subagent_runtime is not None and subagent_runtime.enabled:
        tools.extend(subagent_tools(subagent_runtime))

    return ToolRegistry(tools)


if __name__ == '__main__':
    pass
