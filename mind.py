# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import functools
import os
import typing
from pathlib import Path

from agent.adapters.protocol.compaction import ProtocolCompactionClient
from agent.application import RuntimeServices
from agent.application.approvals.presenter import ApprovalPresenterPort
from agent.application.config.settings import (
    AgentSettings,
    FeatureSettings,
)
from agent.application.turns.run_result import RunResult
from agent.composition import create_runtime_services
from agent.domain.policies import (
    NetworkAccess,
    PermissionSettings,
)
from agent.harness.execution.compaction import compact_conversation
from agent.harness.execution.root_runner import run_root_turn
from agent.harness.hooks.registry import HookRegistry
from agent.harness.process_lifecycle import ProcessLifecycle
from agent.harness.workspace_runtime import WorkspaceRuntimeOwner
from agent.ports import FrontendPort
from agent.ports import (
    HookRegistryPort,
    HookStatusPort,
)
from agent.ports import (
    McpRuntime,
    McpRuntimeContext,
    ProcessCapability,
    SubscriptionHost,
    SubscriptionRuntime,
    ToolRuntimePort,
    ToolRuntimeSources,
)
from agent.ports.network import (
    NetworkBlockedHandlerFactory,
    NetworkPolicyPort,
)
from composition import ApplicationHost
from frontends.cli.entry import run
from frontends.interaction.attachments import Attach
from frontends.mcp.server import run_mind_mcp_server
from frontends.output.silent import create_silent_output_session
from frontends.runtime import FrontendActivity
from frontends.subscription.runtime import AgentRuntime
from frontends.terminal.worked import emit_worked_footer
from frontends.tui.features.conversation import ConversationCompactor
from infrastructure.config.execution_policy_manager import ExecPolicyManager
from infrastructure.config.paths import ApplicationLayout
from infrastructure.config.preferences import Preferences
from infrastructure.config.runtime_paths import effect_journal_db_path
from infrastructure.config.session import ConfigSession
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from infrastructure.mcp.local_tool_factory import (
    build_builtin_tool_registry,
    build_client_tool_registry,
)
from infrastructure.mcp.tool_execution import McpToolExecutionAdapter
from infrastructure.mcp.tool_runtime import CompositeToolRuntime
from infrastructure.platform.animation import AsyncAnimManager
from infrastructure.platform.hook_command import HookCommandExecutor
from infrastructure.platform.images import FileImageReader
from infrastructure.platform.process_sessions import ProcessSessionManager
from infrastructure.platform.sandbox import SandboxClient
from infrastructure.platform.network import ManagedNetworkProxy
from infrastructure.platform.network import StaticNetworkPolicy
from infrastructure.services.turn_environment import (
    capture_active_turn_environment,
    capture_turn_environment,
)
from infrastructure.services.configuration_host import ConfigServiceRuntime
from infrastructure.sidecars.javascript.provider import JavaScriptSidecarProvider
from infrastructure.skills import skills_payload
from infrastructure.workspace.runtime import WorkspaceCoding
from observability.reporting import RunReport


@typing.runtime_checkable
class _ComposedFrontend(FrontendPort, typing.Protocol):
    """描述进程组合根装配应用宿主所需的完整前端能力。"""

    @property
    def interaction(self) -> ApprovalPresenterPort:
        """返回工具审批展示端口。"""
        ...


class _ConfigurationServiceHost(SubscriptionHost, typing.Protocol):
    """描述组合根向订阅适配器注入配置服务地址的最小能力。"""

    def configuration_service_url(self) -> str:
        """返回当前进程内配置服务地址。"""
        ...


def bind_root_turn_runner(
    runtime_services: RuntimeServices,
) -> typing.Callable[..., typing.Awaitable[RunResult]]:
    """在进程组合根绑定根轮次的模型、协议和效果能力。"""
    model_capability = runtime_services.model_capability
    protocol_client = runtime_services.protocol_client
    effect_journal_factory = runtime_services.create_effect_journal

    async def run_bound_root_turn(
        controller: ApplicationHost,
        pref_config: dict[str, typing.Any] | None = None,
        *,
        message: str,
        **kwargs: typing.Any,
    ) -> RunResult:
        """执行绑定进程级能力的根轮次。"""
        return await run_root_turn(
            controller.conversation,
            pref_config,
            message=message,
            model_capability=model_capability,
            protocol_client=protocol_client,
            effect_journal_factory=effect_journal_factory,
            tool_execution=runtime_services.tool_execution,
            approval_coordinator=controller.approval_coordinator,
            execution_policy=controller.workspace_runtime.execution_policy,
            execution_runtime=controller.execution,
            lifecycle=controller.turn_foreground_lifecycle,
            approval_ledger=controller.approval_call_ledger,
            session_factory=controller.frontend.session_factory,
            transcript_factory=controller.conversation.transcript_factory,
            cleanup=controller.conversation,
            patch_preview=controller.workspace_runtime.coding.preview_patch,
            retry_state=controller.frontend.runtime,
            animation=controller.turn_animation,
            session_context=controller,
            session_state=controller.conversation,
            **kwargs,
        )

    return run_bound_root_turn


def bind_conversation_compactor(host: object) -> ConversationCompactor:
    """在进程组合根绑定当前应用宿主的会话压缩用例。"""
    if not isinstance(host, ApplicationHost):
        raise TypeError("conversation compactor host must be ApplicationHost")
    return functools.partial(
        compact_conversation,
        host.conversation,
        ProtocolCompactionClient(),
    )


def create_application_host(
    *,
    config_session: ConfigSession,
    preferences: Preferences,
    permissions: PermissionSettings,
    frontend: _ComposedFrontend,
    report: RunReport,
    hook_registry: HookRegistryPort,
    runtime_services: RuntimeServices,
    application_layout: ApplicationLayout | None,
    configuration_service: ConfigServiceRuntime | None = None,
    workspace_root: str | os.PathLike[str] | None = None,
    animation: AsyncAnimManager | None = None,
    animation_enabled: bool = True,
    hook_startup_warnings: tuple[str, ...] = (),
    hook_status: HookStatusPort | None = None,
    agent_settings: AgentSettings | None = None,
    feature_settings: FeatureSettings | None = None,
) -> ApplicationHost:
    """在唯一进程组合根装配应用宿主的前端侧依赖。"""
    if not isinstance(frontend, _ComposedFrontend):
        raise TypeError("application frontend is incomplete")
    fallback_animation = animation or AsyncAnimManager()
    activity = FrontendActivity(
        frontend.runtime,
        fallback_animation,
        enabled=animation_enabled,
    )
    javascript = create_javascript_provider(
        workspace_root=workspace_root,
        application_layout=application_layout,
    )
    return ApplicationHost(
        workspace_root=(
            os.fspath(workspace_root)
            if workspace_root is not None
            else None
        ),
        application_layout=application_layout,
        runtime_services=runtime_services,
        javascript_execution=javascript,
        javascript_lifecycle=javascript,
        config_session=config_session,
        configuration_service=configuration_service,
        preferences=preferences,
        permissions=permissions,
        frontend=frontend,
        lifecycle=ProcessLifecycle(),
        activity=activity,
        hook_registry=hook_registry,
        report=report,
        approval_presenter=frontend.interaction,
        attachment_state=Attach(),
        subagent_session_factory=create_silent_output_session,
        turn_completion_presenter=emit_worked_footer,
        hook_startup_warnings=hook_startup_warnings,
        hook_status=hook_status,
        agent_settings=agent_settings,
        feature_settings=feature_settings,
    )


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


def create_mcp_runtime(context: McpRuntimeContext) -> McpRuntime:
    """在进程组合根创建绑定显式依赖的 MCP 运行时。"""
    return ExternalMcpRuntime(context)


def create_tool_runtime(sources: ToolRuntimeSources) -> ToolRuntimePort:
    """在进程组合根创建绑定动态工具来源的组合运行时。"""
    return CompositeToolRuntime(sources)


def create_subscription_runtime(
    host: _ConfigurationServiceHost,
    runtime_services: RuntimeServices,
) -> SubscriptionRuntime:
    """在进程组合根创建绑定应用宿主的远端订阅运行时。"""
    return AgentRuntime(
        host,
        configuration_service_url=host.configuration_service_url,
        turn_runner=bind_root_turn_runner(runtime_services),
        environment_snapshot_provider=capture_active_turn_environment,
        turn_application_factory=runtime_services.create_turn_application,
    )


def create_workspace_coding(
    *,
    root: str | os.PathLike[str],
    application_layout: object | None,
    process_capability: ProcessCapability | None = None,
    network_proxy: ManagedNetworkProxy | None = None,
    network_access: NetworkAccess = "restricted",
    network_policy: NetworkPolicyPort | None = None,
    network_blocked_handler_factory: NetworkBlockedHandlerFactory | None = None,
) -> WorkspaceCoding:
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
    effective_network_proxy = network_proxy
    if effective_network_proxy is None and network_access == "restricted":
        effective_network_proxy = ManagedNetworkProxy(
            network_policy or StaticNetworkPolicy(),
        )
    process_sessions = ProcessSessionManager(
        sandbox_client,
        process_capability=process_capability,
        network_proxy=effective_network_proxy,
        network_blocked_handler_factory=network_blocked_handler_factory,
    )
    return WorkspaceCoding(
        root=root,
        process_sessions=process_sessions,
    )


def create_javascript_provider(
    *,
    workspace_root: str | os.PathLike[str] | None,
    application_layout: ApplicationLayout | None,
) -> JavaScriptSidecarProvider:
    """在唯一进程组合根创建 JavaScript Sidecar Provider。"""
    if (
        application_layout is not None
        and not isinstance(application_layout, ApplicationLayout)
    ):
        raise TypeError("application_layout must be ApplicationLayout")
    application_root = (
        application_layout.root
        if application_layout is not None
        else Path(__file__).resolve().parent
    )
    return JavaScriptSidecarProvider(
        workspace_root or Path.cwd(),
        asset_root=application_root / "sidecars" / "js_repl",
        configured_node_path=os.environ.get("JS_REPL_NODE_PATH"),
    )


def create_workspace_runtime(
    workspace_root: str | os.PathLike[str],
    *,
    application_layout: ApplicationLayout | None = None,
    process_capability: ProcessCapability | None = None,
    network_access: NetworkAccess = "restricted",
    network_policy: NetworkPolicyPort | None = None,
    network_blocked_handler_factory: NetworkBlockedHandlerFactory | None = None,
) -> WorkspaceRuntimeOwner:
    """在唯一进程组合根装配本机工作区运行时。"""
    return WorkspaceRuntimeOwner(
        workspace_root,
        application_layout=application_layout,
        coding_factory=create_workspace_coding,
        execution_policy_factory=ExecPolicyManager,
        image_reader_factory=FileImageReader,
        process_capability=process_capability,
        network_access=network_access,
        network_policy=network_policy,
        network_blocked_handler_factory=network_blocked_handler_factory,
    )


if __name__ == "__main__":
    process_runtime_services = create_runtime_services(
        effect_journal_path=effect_journal_db_path(),
        create_hook_registry=create_hook_registry,
        create_client_tool_registry=build_client_tool_registry,
        create_builtin_tool_registry=build_builtin_tool_registry,
        create_tool_runtime=create_tool_runtime,
        tool_execution=McpToolExecutionAdapter(),
        create_mcp_runtime=create_mcp_runtime,
        create_subscription_runtime=create_subscription_runtime,
        skills_payload_builder=skills_payload,
        create_workspace_runtime=create_workspace_runtime,
    )
    root_turn_runner = bind_root_turn_runner(process_runtime_services)
    raise SystemExit(run(
        entry_file=__file__,
        runtime_services=process_runtime_services,
        mcp_server_runner=functools.partial(
            run_mind_mcp_server,
            application_host_factory=create_application_host,
            turn_runner=root_turn_runner,
            environment_snapshot_provider=capture_turn_environment,
        ),
        application_host_factory=create_application_host,
        turn_runner=root_turn_runner,
        environment_snapshot_provider=capture_active_turn_environment,
        conversation_compactor_factory=bind_conversation_compactor,
    ))
