# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from agent.adapters.agents.execution import StreamSubagentExecution
from agent.adapters.protocol.subagent_stream import ProtocolSubagentStream
from agent.application import RuntimeServices
from agent.application.services import SubscriptionRuntimeBuilder
from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.approvals.legacy import DomainApprovalCoordinator
from agent.application.approvals.network import NetworkApprovalService
from agent.application.approvals.presenter import ApprovalPresenterPort
from agent.application.config.settings import (
    AgentSettings,
    FeatureSettings,
)
from agent.application.turns.foreground import (
    ApplicationTurnForegroundLifecycle,
)
from agent.application.turns.durable_queue import (
    DurableQueueApplication,
    DurableQueueSubmissionResult,
    DurableQueueTurnCallbacks,
)
from agent.application.turns.run_result import RunResult
from agent.domain.policies import PermissionSettings
from agent.harness.agents.runtime import SubagentRuntime
from agent.harness.execution.resources import ExecutionResources
from agent.harness.execution.durable_queue import (
    enqueue_durable_root_turn,
    observe_durable_root_turn,
)
from agent.harness.execution.turn_runner import TurnRunner
from agent.harness.hooks.session_lifecycle import SessionLifecycleGateway
from agent.harness.hooks.tool_lifecycle import CommandHookSessionStore
from agent.harness.process_resources import ProcessResourceOwner
from agent.harness.sessions.root import RootConversationSession
from agent.harness.subscription.owner import SubscriptionRuntimeOwner
from agent.ports import (
    ApprovalLedger,
    AttachmentStatePort,
    FrontendActivityPort,
    FrontendPort,
    HookMcpRunnerBinder,
    HookRegistryPort,
    HookScopeProviderPort,
    HookStatusPort,
    JavaScriptExecutionPort,
    JavaScriptSessionLifecyclePort,
    McpRuntimeContext,
    OutputSessionFactory,
    ProcessLifecyclePort,
    ProcessResourcePort,
    SkillsProvider,
    SubscriptionHost,
    SubscriptionRuntime,
    TurnCompletionPresenterPort,
)
from agent.protocol import (
    LocalDurableQueueSnapshot,
    SubmitTurnCommand,
)
from agent.stores import (
    AgentGraphStore,
    SQLiteDurableQueueStore,
)
from agent.stores.approvals.ledger import ApprovalCallLedger
from agent.stores.approvals.facts import SQLiteApprovalFactStore
from agent.stores.approvals.grants import InMemorySessionGrantStore
from agent.stores.approvals.permissions import PermissionGrantStore
from agent.stores.sessions import (
    ConversationHistoryStore,
    normalize_workspace,
)
from infrastructure.config.hooks import HookManager
from infrastructure.config.paths import ApplicationLayout
from infrastructure.config.preferences import Preferences
from infrastructure.config.runtime_paths import (
    agent_graph_db_path,
    approval_fact_db_path,
    conversation_history_db_path,
    durable_queue_db_path,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.settings_session import SettingsSession
from infrastructure.mcp.approval_policy import ConfigMcpPersistentApprovalStore
from infrastructure.mcp.hook_runner import HookMcpRunner as HookMcpRunnerAdapter
from infrastructure.persistence.conversation_history import LocalConversationHistory
from infrastructure.persistence.transcripts import ConversationTranscriptStore
from infrastructure.platform.network import (
    ManagedNetworkRule,
    StaticNetworkPolicy,
)
from infrastructure.services.runtime_owner import ServiceRuntimeOwner
from infrastructure.services.configuration_host import ConfigServiceRuntime
from infrastructure.services.turn_environment import capture_turn_environment
from observability import (
    observe,
    observe_exception,
)
from observability.reporting import RunReport
from protocol.client.reports import EventReportRuntimeOwner

__all__ = ("ApplicationHost",)


def _unconfigured_subscription_runtime(
    _host: SubscriptionHost,
) -> SubscriptionRuntime:
    """返回明确配置错误，禁止入口隐式构造订阅实现。"""
    raise RuntimeError("subscription runtime factory is required")


def _bind_subscription_runtime(
    builder: SubscriptionRuntimeBuilder | None,
    runtime_services: RuntimeServices,
) -> typing.Callable[[SubscriptionHost], SubscriptionRuntime]:
    """把完整订阅组合器绑定为 Harness owner 所需的最小工厂。"""
    if builder is None:
        return _unconfigured_subscription_runtime

    def create(host: SubscriptionHost) -> SubscriptionRuntime:
        """使用当前进程服务创建订阅运行时。"""
        return builder(host, runtime_services)

    return create


def _observe_approval_snapshot_failure(
    error: BaseException,
    coordinator_id: str,
    revision: int,
) -> None:
    """把审批快照通知失败交给统一可观测边界。"""
    observe_exception(
        "approval.snapshot_notify_failed",
        error,
        level="WARNING",
        coordinator_id=coordinator_id,
        revision=revision,
    )


def _observe_resource_close_failure(
    resource: str,
    error: BaseException,
) -> None:
    """把进程资源关闭失败交给统一可观测边界。"""
    observe_exception(
        "runtime.close.failed",
        error,
        resource=resource,
    )


class ApplicationHost:
    """组合进程级协作者，并向各入口暴露职责化端口。

    本类只存在于组合边界，不拥有领域状态机。Session、工具、订阅、前端活动和资源
    关闭分别由对应 owner 持有；调用方不得通过本类添加同义生命周期 facade。
    """

    def __init__(
        self,
        *,
        workspace_root: str | Path | None,
        application_layout: ApplicationLayout | None,
        runtime_services: RuntimeServices,
        javascript_execution: JavaScriptExecutionPort,
        javascript_lifecycle: JavaScriptSessionLifecyclePort,
        config_session: ConfigSession,
        configuration_service: ConfigServiceRuntime | None,
        preferences: Preferences,
        permissions: PermissionSettings,
        frontend: FrontendPort,
        lifecycle: ProcessLifecyclePort,
        activity: FrontendActivityPort,
        hook_registry: HookRegistryPort,
        report: RunReport,
        attachment_state: AttachmentStatePort,
        approval_presenter: ApprovalPresenterPort,
        subagent_session_factory: OutputSessionFactory,
        turn_completion_presenter: TurnCompletionPresenterPort,
        hook_startup_warnings: tuple[str, ...] = (),
        hook_status: HookStatusPort | None = None,
        agent_settings: AgentSettings | None = None,
        feature_settings: FeatureSettings | None = None,
    ) -> None:
        """使用组合根已经选择的具体能力建立唯一应用宿主。"""
        if not isinstance(runtime_services, RuntimeServices):
            raise TypeError("runtime services are required")
        if not isinstance(frontend, FrontendPort):
            raise TypeError("frontend is required")
        if not isinstance(lifecycle, ProcessLifecyclePort):
            raise TypeError("process lifecycle is required")
        if not isinstance(activity, FrontendActivityPort):
            raise TypeError("frontend activity is required")
        if not isinstance(hook_registry, HookRegistryPort):
            raise TypeError("hook registry is required")
        if not isinstance(permissions, PermissionSettings):
            raise TypeError("permission settings are required")

        self.history_workspace = normalize_workspace(workspace_root or Path.cwd())
        self.application_layout = application_layout
        self.runtime_services = runtime_services
        self.durable_queue = DurableQueueApplication(
            runtime_services.durable_queue_client,
            runtime_services.protocol_client,
            SQLiteDurableQueueStore(durable_queue_db_path()),
        )
        self.turn_observer = runtime_services.turn_observer
        self.javascript_execution = javascript_execution
        self.javascript_lifecycle = javascript_lifecycle
        self.frontend = frontend
        self.lifecycle = lifecycle
        self.activity = activity
        self.report = report
        self.attach = attachment_state
        self.hook_startup_warnings = tuple(hook_startup_warnings)
        self.features = feature_settings or FeatureSettings()

        self.settings = SettingsSession(
            config_session,
            preferences,
            permissions,
        )
        self.configuration_service = configuration_service
        skills_provider_factory = runtime_services.create_skills_provider
        self._skills_provider: SkillsProvider | None = (
            skills_provider_factory(self.settings.config.load)
            if skills_provider_factory is not None
            else None
        )

        self.hooks = HookManager(
            self.settings.config,
            hook_registry,
            workspace=lambda: self.history_workspace,
            status_port=hook_status,
        )
        self.command_hook_sessions = CommandHookSessionStore()
        self.turn_foreground_lifecycle = ApplicationTurnForegroundLifecycle(
            self.frontend,
            self.activity,
            self.lifecycle,
            turn_completion_presenter,
        )

        history_store = ConversationHistoryStore(conversation_history_db_path())
        transcripts = ConversationTranscriptStore()
        event_reporting = EventReportRuntimeOwner()

        self.subagent_execution = StreamSubagentExecution(
            ProtocolSubagentStream(
                model_capability=runtime_services.model_capability,
                protocol_client=runtime_services.protocol_client,
                effect_journal_factory=runtime_services.create_effect_journal,
                tool_execution=runtime_services.tool_execution,
                session_factory=subagent_session_factory,
            )
        )
        self.session_lifecycle = SessionLifecycleGateway(
            scope_factory=self.hooks.hook_scope,
            cleanup_session=self.hooks.cleanup_session,
        )
        legacy_approval_coordinator = ApprovalCoordinator(
            approval_presenter,
            snapshot_error_handler=_observe_approval_snapshot_failure,
        )
        self.approval_coordinator = DomainApprovalCoordinator(
            legacy_approval_coordinator,
            fact_store=SQLiteApprovalFactStore(approval_fact_db_path()),
            grant_store=InMemorySessionGrantStore(),
            persistent_mcp_approvals=ConfigMcpPersistentApprovalStore(
                config_session
            ),
        )
        self.approval_call_ledger = ApprovalCallLedger()
        self.permission_grants = PermissionGrantStore()

        network_policy = StaticNetworkPolicy()
        network_service_holder: dict[str, NetworkApprovalService] = {}

        def network_blocked_handler_factory(
            session_id: str,
            run_id: str,
            environment_id: str,
            execution_id: str,
        ):
            """为受管进程创建绑定审批服务的网络阻断回调。"""
            service = network_service_holder.get("service")
            if service is None:
                return None
            try:
                return service.handler(
                    session_id=session_id,
                    run_id=run_id,
                    environment_id=environment_id,
                    execution_id=execution_id,
                )
            except ValueError:
                return None

        async def persist_network_rule(rule: ManagedNetworkRule) -> None:
            """把网络持久允许交给当前执行策略的 Effect owner。"""
            execution_policy = self.workspace_runtime.execution_policy
            persist = getattr(execution_policy, "persist_network_rule", None)
            if not callable(persist):
                raise RuntimeError("network policy persistence is unavailable")
            persist(rule)

        workspace_runtime_factory = runtime_services.create_workspace_runtime
        if workspace_runtime_factory is None:
            raise TypeError("workspace runtime factory is required")
        self.workspace_runtime = workspace_runtime_factory(
            self.history_workspace,
            application_layout=self.application_layout,
            process_capability=runtime_services.process_capability,
            network_access=self.settings.permissions.network_access,
            network_policy=network_policy,
            network_blocked_handler_factory=network_blocked_handler_factory,
        )
        network_service_holder["service"] = NetworkApprovalService(
            self.approval_coordinator,
            network_policy,
            rule_sink=persist_network_rule,
        )
        self.network_approval_service = network_service_holder["service"]

        mcp_runtime_builder = runtime_services.create_mcp_runtime
        mcp_runtime_context = McpRuntimeContext(
            config=self.settings.config,
            start_activity=self.activity.start_external_mcp,
            stop_activity=self.activity.stop,
            await_cleanup=self.lifecycle.await_cleanup,
        )
        external_runtime_factory = (
            (lambda: mcp_runtime_builder(mcp_runtime_context))
            if mcp_runtime_builder is not None
            else None
        )
        self.execution = ExecutionResources(
            event_reporting=event_reporting,
            tool_runtime_builder=runtime_services.create_tool_runtime,
            client_registry_factory=(
                lambda: runtime_services.create_client_tool_registry(
                    self.workspace_runtime.coding,
                    javascript=self.javascript_execution,
                    image_reader=self.workspace_runtime.image_reader,
                    execution_policy=self.workspace_runtime.execution_policy,
                    subagent_runtime=self.subagents,
                    approval_coordinator=self.approval_coordinator,
                    features=self.features,
                )
            ),
            builtin_registry_factory=(
                lambda: runtime_services.create_builtin_tool_registry(
                    approval_coordinator=self.approval_coordinator,
                    permission_grants=self.permission_grants,
                    features=self.features,
                )
            ),
            external_runtime_factory=external_runtime_factory,
            await_cleanup=self.lifecycle.await_cleanup,
        )
        if isinstance(hook_registry, HookMcpRunnerBinder):
            hook_registry.bind_mcp_runner(
                HookMcpRunnerAdapter(self.execution.external_tool_group)
            )

        history = LocalConversationHistory(
            history_store,
            existing_transcript_path_for=transcripts.existing_path_for_session,
            transcript_entries_for=lambda path: transcripts.reader(path).read(),
        )
        self.conversation = RootConversationSession(
            history,
            workspace=lambda: self.history_workspace,
            permissions=lambda: self.settings.permissions,
            preference_config=self.settings.preference_config,
            fresh_preferences=(
                lambda ttl_sec: self.settings.fresh_preferences(ttl_sec=ttl_sec)
            ),
            permission_grants=self.permission_grants,
            approval_ledger=(
                self.approval_call_ledger
                if isinstance(self.approval_call_ledger, ApprovalLedger)
                else None
            ),
            output_record_path=str(self.report.output_record_path or ""),
            transcript_factory=transcripts.writer,
            transcript_path_for=transcripts.path_for_session,
            hook_scope_provider=self.hooks,
            session_lifecycle=self.session_lifecycle,
            subagent_shutdown=lambda sid: self.subagents.shutdown_root(sid),
            hook_session_cleanup=self.hooks.cleanup_session,
            javascript_session_cleanup=self.javascript_lifecycle.close_session,
            command_hook_cleanup=self.command_hook_sessions.clear_root,
            event_session_close=self.execution.event_reporting.close_session,
            await_cleanup=self.lifecycle.await_cleanup,
        )
        self.subagent_turn_runner = TurnRunner(self.execution)
        self.subagent_cleanup = self.conversation
        self.subagents = SubagentRuntime(
            self,
            enabled=self.features.subagents,
            settings=agent_settings or AgentSettings(),
            execution_policy=self.workspace_runtime.execution_policy,
            approval_coordinator=self.approval_coordinator,
            permission_grants=self.permission_grants,
            approval_ledger=(
                self.approval_call_ledger
                if isinstance(self.approval_call_ledger, ApprovalLedger)
                else None
            ),
            transcript_factory=self.conversation.transcript_factory,
            cleanup=self.conversation,
            patch_preview=self.workspace_runtime.coding.preview_patch,
            skills_provider=self._skills_provider,
            transcript_path_for=self.conversation.transcript_path_for_session,
            transcript_entries_for=lambda path: transcripts.reader(path).read(),
            javascript_session_cleanup=self.javascript_lifecycle.close_session,
            graph_store=AgentGraphStore(
                agent_graph_db_path(),
                ttl_ms=self.conversation.history_ttl_ms,
                max_items=self.conversation.history_max_items,
            ),
        )
        self.service_runtime = ServiceRuntimeOwner()

        self.subscription = SubscriptionRuntimeOwner(
            self,
            runtime_factory=_bind_subscription_runtime(
                runtime_services.create_subscription_runtime,
                runtime_services,
            ),
        )
        self.resources: ProcessResourcePort = ProcessResourceOwner(
            close_subscription=self.subscription.close,
            cancel_service_startup=self.service_runtime.cancel_startup,
            shutdown_subagents=self.subagents.shutdown,
            close_approvals=self.approval_coordinator.close,
            clear_command_hooks=self.command_hook_sessions.clear,
            close_hooks=self.hooks.close,
            close_javascript=self.javascript_lifecycle.close,
            close_workspace=self.workspace_runtime.close,
            close_execution=self.execution.close,
            close_service=self._close_services,
            observe_failure=_observe_resource_close_failure,
        )

        observe(
            "application_host.ready",
            run_id=self.report.run_id,
            workspace=self.history_workspace,
            animate=self.activity.enabled,
            client_tools=self.execution.client_tool_count(),
        )

    async def _close_services(self) -> None:
        """按组合根顺序关闭内置配置宿主和 Helix 服务。"""
        configuration_service = self.configuration_service
        try:
            if configuration_service is not None:
                await configuration_service.stop()
        finally:
            await self.service_runtime.close()

    async def enqueue_durable_turn(
        self,
        command: SubmitTurnCommand,
        *,
        permissions: PermissionSettings,
        submission_id: str,
        client_message_id: str,
        request_id: str,
    ) -> DurableQueueSubmissionResult:
        """使用当前组合能力冻结并提交一项显式持久 Queue 输入。"""
        return await enqueue_durable_root_turn(
            self.conversation,
            self.durable_queue,
            command,
            permissions=permissions,
            execution_runtime=self.execution,
            approval_coordinator=self.approval_coordinator,
            execution_policy=self.workspace_runtime.execution_policy,
            transcript_factory=self.conversation.transcript_factory,
            cleanup=self.conversation,
            patch_preview=self.workspace_runtime.coding.preview_patch,
            session_context=self,
            session_state=self.conversation,
            submission_id=submission_id,
            client_message_id=client_message_id,
            request_id=request_id,
        )

    async def observe_durable_turn(
        self,
        local: LocalDurableQueueSnapshot,
        *,
        callbacks: DurableQueueTurnCallbacks,
    ) -> RunResult:
        """只观察 Queue start 已创建的远端 Turn 并执行客户端工具。"""
        services = self.runtime_services
        return await observe_durable_root_turn(
            self.conversation,
            local,
            model_capability=services.model_capability,
            turn_observer=services.turn_observer,
            protocol_client=services.protocol_client,
            effect_journal_factory=services.create_effect_journal,
            tool_execution=services.tool_execution,
            approval_coordinator=self.approval_coordinator,
            execution_policy=self.workspace_runtime.execution_policy,
            execution_runtime=self.execution,
            lifecycle=self.turn_foreground_lifecycle,
            session_factory=self.frontend.session_factory,
            transcript_factory=self.conversation.transcript_factory,
            cleanup=self.conversation,
            patch_preview=self.workspace_runtime.coding.preview_patch,
            session_context=self,
            session_state=self.conversation,
            callbacks=callbacks,
        )

    def configuration_service_url(self) -> str:
        """返回已启动的内置配置服务地址。"""
        configuration_service = self.configuration_service
        if configuration_service is None:
            raise RuntimeError("configuration service is not available")
        return configuration_service.address.base_url

    @property
    def animate(self) -> bool:
        """返回当前入口是否启用活动展示。"""
        return self.activity.enabled

    @property
    def workspace_root(self) -> str:
        """返回当前根轮次绑定的工作区。"""
        return self.history_workspace

    @property
    def hook_scope_provider(self) -> HookScopeProviderPort:
        """返回根轮次和子 Agent 共享的 Hook 作用域提供器。"""
        return self.hooks

    def capture_environment(
        self,
        *,
        cwd: str,
        workspace_root: str,
    ) -> dict[str, typing.Any] | None:
        """捕获当前轮次使用的客户端环境快照。"""
        return capture_turn_environment(
            self,
            cwd=Path(cwd),
            workspace_root=Path(workspace_root),
        )

    def set_history_workspace(self, workspace: str | Path) -> str:
        """原子替换后续 Turn 使用的工作区能力和工具注册表。"""
        normalized = normalize_workspace(workspace)
        if normalized and normalized != self.history_workspace:
            self.workspace_runtime.replace(normalized)
            self.history_workspace = normalized
            self.command_hook_sessions.clear()
            self.execution.rebuild_client_registry()
            observe("workspace.changed", workspace=self.history_workspace)
        return self.history_workspace

    def skills_payload(self) -> list[dict[str, typing.Any]]:
        """返回当前配置对应的模型可见 skills 快照。"""
        if self._skills_provider is None:
            return []
        return list(self._skills_provider())


if __name__ == '__main__':
    pass
