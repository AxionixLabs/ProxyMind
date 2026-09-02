# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.application.tools.definitions import ClientTool
from agent.application.tools.patching import patch_tools
from agent.application.tools.processes import process_tools
from agent.ports.workspace import WorkspaceCodingPort

__all__ = ("coding_tools",)


def coding_tools(
    coding: WorkspaceCodingPort,
    *,
    exec_permission_approvals_enabled: bool = False,
) -> list[ClientTool]:
    """组合共享同一工作区生命周期的进程和补丁工具能力族。"""
    return [
        *process_tools(
            coding,
            exec_permission_approvals_enabled=exec_permission_approvals_enabled,
        ),
        *patch_tools(coding),
    ]


if __name__ == '__main__':
    pass
