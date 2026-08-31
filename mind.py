# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import functools

from agent.composition import create_runtime_services
from agent.harness.workspace_runtime import WorkspaceRuntimeOwner
from agent.ports import (
    McpRuntime,
    McpRuntimeHost,
    ProcessCapability,
    SubscriptionHost,
    SubscriptionRuntime,
)
from infrastructure.skills import skills_payload
from infrastructure.config.paths import ApplicationLayout
from infrastructure.platform.process_sessions import ProcessSessionManager
from infrastructure.platform.sandbox import SandboxClient
from infrastructure.platform.hook_command import HookCommandExecutor
from mind_app.runtime.mcp.external import ExternalMcpRuntime
from mind_app.cli.entry import run
from frontends.mcp.server import run_mind_mcp_server
from mind_app.native_coding import NativeCoding
from infrastructure.config.execution_policy_manager import ExecPolicyManager
from agent.harness.hooks.registry import HookRegistry
from mind_app.interaction.environment import (
    capture_active_turn_environment,
    capture_turn_environment,
)
from mind_app.runtime.turns.root import run_root_turn
from frontends.subscription.runtime import AgentRuntime


def create_hook_registry(*, bypass_hook_trust: bool = False) -> HookRegistry:
    """在进程组合根创建绑定本机资源的 Hook registry。"""
    command_runner = HookCommandExecutor()
    return HookRegistry(
        command_runner=command_runner,
        context_spiller=command_runner,
        cleanup_session=command_runner.cleanup_session,
        close=command_runner.close,
        bypass_hook_trust=bypass_hook_trust,
    )


def create_mcp_runtime(host: McpRuntimeHost) -> McpRuntime:
    """在进程组合根创建绑定应用生命周期端口的 MCP 运行时。"""
    return ExternalMcpRuntime(host)


def create_subscription_runtime(host: SubscriptionHost) -> SubscriptionRuntime:
    """在进程组合根创建绑定应用宿主的远端订阅运行时。"""
    return AgentRuntime(
        host,
        turn_runner=run_root_turn,
        environment_snapshot_provider=capture_active_turn_environment,
    )


def create_native_coding(
    *,
    root: str | os.PathLike[str],
    application_layout: object | None,
    process_capability: ProcessCapability | None = None,
) -> NativeCoding:
    """在进程组合根创建绑定工作区的平台执行资源。"""
    if (
        application_layout is not None
        and not isinstance(application_layout, ApplicationLayout)
    ):
        raise TypeError("application_layout must be ApplicationLayout")
    sandbox_client = SandboxClient(
        workspace_root=root,
        application_root=(
            application_layout.root if application_layout is not None else None
        ),
        packaged=(
            application_layout.packaged if application_layout is not None else None
        ),
        platform=(
            application_layout.platform if application_layout is not None else None
        ),
    )
    process_sessions = ProcessSessionManager(
        sandbox_client,
        process_capability=process_capability,
    )
    return NativeCoding(
        root=root,
        application_layout=application_layout,
        process_sessions=process_sessions,
    )


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
        coding_factory=create_native_coding,
        execution_policy_factory=ExecPolicyManager,
        process_capability=process_capability,
    )


if __name__ == "__main__":
    raise SystemExit(run(
        entry_file=__file__,
        runtime_services=create_runtime_services(
            create_hook_registry=create_hook_registry,
            create_mcp_runtime=create_mcp_runtime,
            create_subscription_runtime=create_subscription_runtime,
            skills_payload_builder=skills_payload,
            create_workspace_runtime=create_workspace_runtime,
        ),
        mcp_server_runner=functools.partial(
            run_mind_mcp_server,
            turn_runner=run_root_turn,
            environment_snapshot_provider=capture_turn_environment,
        ),
    ))
