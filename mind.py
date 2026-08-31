# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os

from agent.composition import create_runtime_services
from agent.harness.workspace_runtime import WorkspaceRuntimeOwner
from agent.ports import ProcessCapability
from infrastructure.config.paths import ApplicationLayout
from mind_app.cli.entry import run
from mind_app.native_coding import NativeCoding
from mind_app.native_coding.exec.exec_policy import ExecPolicyManager
from mind_app.runtime.hooks.registry import HookRegistry


def create_hook_registry(*, bypass_hook_trust: bool = False) -> HookRegistry:
    """在进程组合根创建绑定本机资源的 Hook registry。"""
    return HookRegistry(bypass_hook_trust=bypass_hook_trust)


def create_workspace_runtime(
    workspace_root: str | os.PathLike[str],
    *,
    application_layout: ApplicationLayout | None = None,
    process_capability: ProcessCapability | None = None,
) -> WorkspaceRuntimeOwner:
    """在唯一进程组合根装配本机工作区运行时。"""
    return WorkspaceRuntimeOwner(
        workspace_root,
        application_layout=application_layout,
        coding_factory=NativeCoding,
        execution_policy_factory=ExecPolicyManager,
        process_capability=process_capability,
    )


if __name__ == "__main__":
    raise SystemExit(run(
        entry_file=__file__,
        runtime_services=create_runtime_services(
            create_hook_registry=create_hook_registry,
            create_workspace_runtime=create_workspace_runtime,
        ),
    ))
