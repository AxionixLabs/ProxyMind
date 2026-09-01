# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import functools
import typing

from agent.composition import create_runtime_services
from agent.application import RuntimeServices
from agent.application.turns.run_result import RunResult
from agent.ports import (
    ModelCapability,
    ProtocolCommandClient,
)
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
from frontends.cli.entry import run
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

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


def bind_root_turn_runner(
    runtime_services: RuntimeServices,
) -> typing.Callable[..., typing.Awaitable[RunResult]]:
    """在进程组合根绑定根轮次的模型、协议和效果能力。"""
    model_capability = runtime_services.model_capability
    if not isinstance(model_capability, ModelCapability):
        raise TypeError("root turn model capability is invalid")
    if not isinstance(model_capability, ProtocolCommandClient):
        raise TypeError("root turn protocol client is invalid")
    effect_journal_factory = runtime_services.create_effect_journal

    async def run_bound_root_turn(
        controller: "Mind",
        pref_config: dict[str, typing.Any] | None = None,
        *,
        message: str,
        **kwargs: typing.Any,
    ) -> RunResult:
        """执行绑定进程级能力的根轮次。"""
        return await run_root_turn(
            controller,
            pref_config,
            message=message,
            model_capability=model_capability,
            protocol_client=model_capability,
            effect_journal_factory=effect_journal_factory,
            approval_ledger=controller.approval_call_ledger,
            session_factory=controller.frontend.session_factory,
            transcript_factory=controller.transcripts.writer,
            cleanup=controller,
            patch_preview=controller.workspace_runtime.coding.preview_patch,
            retry_state=controller.frontend.runtime,
            **kwargs,
        )

    return run_bound_root_turn


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
    runtime_services = getattr(host, "runtime_services", None)
    turn_application_factory = getattr(
        runtime_services,
        "create_turn_application",
        None,
    )
    if not callable(turn_application_factory):
        raise RuntimeError("subscription turn application factory is required")
    return AgentRuntime(
        host,
        turn_runner=bind_root_turn_runner(runtime_services),
        environment_snapshot_provider=capture_active_turn_environment,
        turn_application_factory=turn_application_factory,
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
    runtime_services = create_runtime_services(
        create_hook_registry=create_hook_registry,
        create_mcp_runtime=create_mcp_runtime,
        create_subscription_runtime=create_subscription_runtime,
        skills_payload_builder=skills_payload,
        create_workspace_runtime=create_workspace_runtime,
    )
    root_turn_runner = bind_root_turn_runner(runtime_services)
    raise SystemExit(run(
        entry_file=__file__,
        runtime_services=runtime_services,
        mcp_server_runner=functools.partial(
            run_mind_mcp_server,
            turn_runner=root_turn_runner,
            environment_snapshot_provider=capture_turn_environment,
        ),
        turn_runner=root_turn_runner,
        environment_snapshot_provider=capture_active_turn_environment,
    ))
