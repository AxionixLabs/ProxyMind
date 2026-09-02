# -*- coding: utf-8 -*-
# Notes: ==== Mind(TM) ====

from agent.application.tools.definitions import ClientTool
from agent.application.tools.javascript import javascript_tools
from agent.application.tools.patching import patch_tools
from agent.application.tools.processes import process_tools
from agent.ports.approvals import ApprovalCoordinatorPort
from agent.ports.workspace import (
    ExecutionPolicy,
    WorkspaceCodingPort,
)


def coding_tools(
    coding: WorkspaceCodingPort,
    *,
    approval_coordinator: ApprovalCoordinatorPort | None = None,
    execution_policy: ExecutionPolicy | None = None,
    exec_permission_approvals_enabled: bool = False,
) -> list[ClientTool]:
    """组合共享同一工作区生命周期的编码工具能力族。"""
    return [
        *javascript_tools(
            coding,
            approval_coordinator=approval_coordinator,
            execution_policy=execution_policy,
        ),
        *process_tools(
            coding,
            exec_permission_approvals_enabled=exec_permission_approvals_enabled,
        ),
        *patch_tools(coding),
    ]


__all__ = ("coding_tools",)

if __name__ == "__main__":
    pass
