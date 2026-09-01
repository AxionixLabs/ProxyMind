# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from pathlib import Path
from infrastructure.platform.animation import AsyncAnimManager
from infrastructure.config.preferences import Preferences
from infrastructure.config.paths import ApplicationLayout
from infrastructure.config.session import ConfigSession
from agent.application.config.settings import (
    AgentSettings,
    FeatureSettings,
)
from agent.application.turns.foreground import (
    ApplicationTurnForegroundLifecycle,
    FrontendTurnAnimation,
)
from agent.domain.policies import (
    PermissionSettings,
    resolve_permissions
)
from observability.reporting import RunReport
from observability import (
    observe,
    observe_exception
)
from infrastructure.services.runtime_owner import ServiceRuntimeOwner
from infrastructure.services.turn_environment import capture_turn_environment
from protocol.client.reports import EventReportRuntimeOwner
from agent.harness.execution.resources import ExecutionResources
from agent.harness.sessions.root import RootConversationSession
from agent.stores.approvals.permissions import PermissionGrantStore
from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.stores.approvals.ledger import ApprovalCallLedger
from agent.harness.agents.runtime import SubagentRuntime
from agent.adapters.agents.execution import StreamSubagentExecution
from agent.adapters.protocol.subagent_stream import ProtocolSubagentStream
from agent.harness.execution.turn_runner import TurnRunner
from agent.stores import AgentGraphStore
from infrastructure.config.runtime_paths import (
    agent_graph_db_path,
    mind_history_db_path,
)
from agent.harness.subscription.owner import SubscriptionRuntimeOwner
from agent.ports.frontend import (
    ActivityStatusKind,
    AttachmentStatePort,
    FrontendPort,
    TurnCompletionPresenterPort,
)
from agent.ports import (
    ApprovalLedger,
    HookScopeProviderPort,
    HookRegistryPort,
    OutputSessionFactory,
    ProtocolCommandClient,
    SkillsProvider,
)
from agent.harness.hooks.session_lifecycle import SessionLifecycleGateway
from agent.harness.hooks.tool_lifecycle import CommandHookSessionStore
from agent.stores.sessions import (
    ConversationHistoryStore,
    normalize_workspace
)
from infrastructure.persistence.transcripts import (
    ConversationTranscriptStore,
)
from infrastructure.persistence.conversation_history import (
    LocalConversationHistory,
)
from infrastructure.config.hooks import HookManager
from agent.ports import (
    SubscriptionHost,
    SubscriptionRuntime,
)

CleanupResult = typing.TypeVar("CleanupResult")


def _unconfigured_subscription_runtime(_host: SubscriptionHost) -> SubscriptionRuntime:
    """返回明确配置错误，禁止入口隐式构造订阅实现。"""
    raise RuntimeError("subscription runtime factory is required")


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


class Mind(object):
    """维护共享状态，并暴露稳定的应用接口。"""

    __remote: dict = {}

    def __init__(self, level: str, power: int, remote: dict, **kwargs):
        self.level = level
        self.power = power

        self.remote: dict = remote or {}

        self.src_opera_place: str = kwargs["src_opera_place"]
        self.src_total_place: str = kwargs["src_total_place"]

        self.history_workspace: str = normalize_workspace(
            kwargs.get("workspace_root") or Path.cwd()
        )
        self.application_layout: ApplicationLayout | None = kwargs.get(
            "application_layout"
        )
        self.runtime_services = kwargs.get("runtime_services")
        if self.runtime_services is None:
            raise ValueError("Agent runtime services are required")

        self.pref: Preferences = kwargs["pref"]
        self.config_session: ConfigSession = kwargs["config_session"]
        self.permissions: PermissionSettings = kwargs["permissions"]
        self.frontend: FrontendPort = kwargs["frontend"]
        skills_provider_factory = getattr(
            self.runtime_services,
            "create_skills_provider",
            None,
        )
        self._skills_provider: SkillsProvider | None = (
            skills_provider_factory(self.config_session.load)
            if callable(skills_provider_factory)
            else None
        )

        hook_registry = kwargs.get("hook_registry")
        if not isinstance(hook_registry, HookRegistryPort):
            raise TypeError("hook registry is required")
        self.hook_startup_warnings = tuple(
            kwargs.get("hook_startup_warnings") or ()
        )
        self.hooks = HookManager(
            self.config_session,
            hook_registry,
            workspace=lambda: self.history_workspace,
            status_port=kwargs.get("hook_status"),
        )

        self.command_hook_sessions = CommandHookSessionStore()

        self.pref_refreshed_at: float = time.monotonic()
        self.pref_refresh_ttl_sec: float = 1.0

        self.task_event: asyncio.Event = asyncio.Event()

        self.anim_manager: AsyncAnimManager = kwargs.get("anim_manager") or AsyncAnimManager()

        self.animate: bool = bool(kwargs.get("animate", True))
        self.turn_animation = FrontendTurnAnimation(
            self.frontend.runtime,
            self.stop_anim,
        )
        turn_completion_presenter: TurnCompletionPresenterPort = kwargs[
            "turn_completion_presenter"
        ]
        self.turn_foreground_lifecycle = ApplicationTurnForegroundLifecycle(
            self,
            turn_completion_presenter,
        )
        history_store: ConversationHistoryStore = (
            kwargs.get("history_store")
            or ConversationHistoryStore(mind_history_db_path())
        )

        transcripts: ConversationTranscriptStore = (
            kwargs.get("transcript_store") or ConversationTranscriptStore()
        )
        event_reporting = EventReportRuntimeOwner(
            pool=kwargs.get("event_report_pool"),
        )
        subagent_session_factory: OutputSessionFactory = kwargs[
            "subagent_session_factory"
        ]
        model_capability = self.runtime_services.model_capability
        if not isinstance(model_capability, ProtocolCommandClient):
            raise TypeError("subagent protocol client is required")
        self.subagent_execution = StreamSubagentExecution(
            ProtocolSubagentStream(
                model_capability=model_capability,
                protocol_client=model_capability,
                effect_journal_factory=(
                    self.runtime_services.create_effect_journal
                ),
                tool_execution=self.runtime_services.tool_execution,
                session_factory=subagent_session_factory,
            )
        )
        self.session_lifecycle = SessionLifecycleGateway(
            scope_factory=self.hooks.hook_scope,
            cleanup_session=self.hooks.cleanup_session,
        )

        self.report: RunReport = kwargs["report"]

        self.attach: AttachmentStatePort = kwargs["attachment_state"]

        self.approval_coordinator = ApprovalCoordinator(
            kwargs["approval_presenter"],
            snapshot_error_handler=_observe_approval_snapshot_failure,
        )
        self.approval_call_ledger = ApprovalCallLedger()

        self.features: FeatureSettings = (
            kwargs.get("feature_settings") or FeatureSettings()
        )

        workspace_runtime_factory = getattr(
            self.runtime_services,
            "create_workspace_runtime",
            None,
        )
        if not callable(workspace_runtime_factory):
            raise TypeError("workspace runtime factory is required")
        self.workspace_runtime = workspace_runtime_factory(
            self.history_workspace,
            application_layout=self.application_layout,
            process_capability=getattr(
                self.runtime_services,
                "process_capability",
                None,
            ),
        )
        self.permission_grants = PermissionGrantStore()

        mcp_runtime_builder = self.runtime_services.create_mcp_runtime
        external_runtime_factory = (
            (lambda: mcp_runtime_builder(self))
            if callable(mcp_runtime_builder)
            else None
        )
        self.execution = ExecutionResources(
            event_reporting=event_reporting,
            tool_runtime_builder=self.runtime_services.create_tool_runtime,
            client_registry_factory=(
                lambda: self.runtime_services.create_client_tool_registry(
                    self.workspace_runtime.coding,
                    image_reader=self.workspace_runtime.image_reader,
                    execution_policy=self.workspace_runtime.execution_policy,
                    subagent_runtime=self.subagents,
                    approval_coordinator=self.approval_coordinator,
                    features=self.features,
                )
            ),
            builtin_registry_factory=(
                lambda: self.runtime_services.create_builtin_tool_registry(
                    approval_coordinator=self.approval_coordinator,
                    permission_grants=self.permission_grants,
                    features=self.features,
                )
            ),
            external_runtime_factory=external_runtime_factory,
            await_cleanup=self.await_cleanup,
        )
        history = LocalConversationHistory(
            history_store,
            existing_transcript_path_for=transcripts.existing_path_for_session,
            transcript_entries_for=(
                lambda path: transcripts.reader(path).read()
            ),
        )
        self.conversation = RootConversationSession(
            history,
            workspace=lambda: self.history_workspace,
            permissions=lambda: self.permissions,
            preference_config=self.pref.to_config,
            fresh_preferences=(
                lambda ttl_sec: self.fresh_pref_config(ttl_sec=ttl_sec)
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
            execution_session_cleanup=(lambda sid: (
                self.workspace_runtime.coding.close_js_repl_session(sid)
            )),
            command_hook_cleanup=self.command_hook_sessions.clear_root,
            event_session_close=self.execution.event_reporting.close_session,
            await_cleanup=self.await_cleanup,
        )
        self.subagent_turn_runner = TurnRunner(self.execution)
        self.subagent_cleanup = self.conversation

        subagent_runtime = kwargs.get("subagent_runtime")
        if subagent_runtime is None:
            subagent_runtime = SubagentRuntime(
                self,
                enabled=self.features.subagents,
                settings=kwargs.get("agent_settings") or AgentSettings(),
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
                transcript_entries_for=(
                    lambda path: transcripts.reader(path).read()
                ),
                session_cleanup=(lambda sid: (
                    self.workspace_runtime.coding.close_js_repl_session(sid)
                )),
                graph_store=(
                    kwargs.get("agent_graph_store")
                    or AgentGraphStore(
                        agent_graph_db_path(),
                        ttl_ms=self.conversation.history_ttl_ms,
                        max_items=self.conversation.history_max_items,
                    )
                ),
            )
        self.subagents: SubagentRuntime = subagent_runtime
        self.service_runtime = ServiceRuntimeOwner()

        subscription_factory = getattr(
            self.runtime_services,
            "create_subscription_runtime",
            None,
        )
        if not callable(subscription_factory):
            subscription_factory = _unconfigured_subscription_runtime
        self.subscription = SubscriptionRuntimeOwner(
            self,
            runtime_factory=subscription_factory,
        )

        self.exit_code: int = 0

        observe(
            "controller.ready",
            run_id=getattr(self.report, "run_id", None),
            workspace=self.history_workspace,
            animate=self.animate,
            client_tools=self.execution.client_tool_count(),
        )

    @property
    def remote(self) -> dict:
        """返回远程全局配置。"""
        return self.__remote

    @remote.setter
    def remote(self, value: dict) -> None:
        """设置远程全局配置，并在异常输入时兜底为空字典。"""
        self.__remote = value if isinstance(value, dict) else {}

    @staticmethod
    async def await_cleanup(awaitable: typing.Awaitable[CleanupResult]) -> CleanupResult:
        """在取消态下也等待清理逻辑执行完成。"""
        task = asyncio.ensure_future(awaitable)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 1:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            else:
                await task
            raise

    def set_history_workspace(self, workspace: typing.Any) -> str:
        """更新 history 使用的真实工作区根目录。"""
        normalized = normalize_workspace(workspace)

        if normalized and normalized != self.history_workspace:
            self.workspace_runtime.replace(normalized)
            self.history_workspace = normalized

            self.command_hook_sessions.clear()
            self.execution.rebuild_client_registry()

            observe("workspace.changed", workspace=self.history_workspace)

        return self.history_workspace

    @property
    def workspace_root(self) -> str:
        """返回当前根轮次绑定的工作区。"""
        return str(self.history_workspace or "")

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

    def skills_payload(self) -> list[dict[str, typing.Any]]:
        """返回当前配置对应的模型可见 skills 快照。"""
        if self._skills_provider is None:
            return []
        return list(self._skills_provider())

    @property
    def hook_scope_provider(self) -> HookScopeProviderPort:
        """返回根轮次和子 Agent 共享的 Hook 作用域提供器。"""
        return self.hooks

    def apply_permissions(self, settings: PermissionSettings) -> PermissionSettings:
        """原子保存权限设置并同步当前控制器状态。"""
        if not isinstance(settings, PermissionSettings):
            raise TypeError("permission settings are required")

        effective_config = self.config_session.update_user({
            ("sandbox_mode",): settings.sandbox_mode,
            ("approval_policy",): settings.approval_policy,
            ("approvals_reviewer",): settings.approvals_reviewer,
            ("network_access",): settings.network_access,
        }, ensure_effective={
            ("sandbox_mode",): settings.sandbox_mode,
            ("approval_policy",): settings.approval_policy,
            ("approvals_reviewer",): settings.approvals_reviewer,
            ("network_access",): settings.network_access,
        })
        effective = resolve_permissions(effective_config, interactive=True)
        self.permissions = effective
        return effective

    async def refresh_pref_if_stale(
        self,
        *,
        ttl_sec: typing.Optional[float] = None
    ) -> None:
        """按 TTL 从后端刷新偏好配置，用于模型与密钥热更新。"""
        refresh_ttl = self.pref_refresh_ttl_sec if ttl_sec is None else max(0.0, float(ttl_sec))

        now = time.monotonic()

        if self.pref_refreshed_at and (now - self.pref_refreshed_at) < refresh_ttl:
            return None

        try:
            await self.pref.load_pref()
        except Exception as exc:
            observe_exception("preferences.refresh.failed", exc, level="WARNING")
            return None

        self.pref_refreshed_at = time.monotonic()

    async def fresh_pref_config(
        self,
        *,
        ttl_sec: typing.Optional[float] = None
    ) -> dict[str, typing.Any]:
        """返回刷新后的偏好配置快照。"""
        await self.refresh_pref_if_stale(ttl_sec=ttl_sec)
        return self.pref.to_config()

    async def close_runtime_resources(self) -> None:
        """关闭主控制器持有的运行时资源，并按退出策略处理本地后台进程。"""
        observe("runtime.close.start")
        try:
            await self.subscription.close()
            await self.service_runtime.cancel_startup()

            await self.subagents.shutdown()
            approval_coordinator = getattr(
                self,
                "approval_coordinator",
                None,
            )
            if approval_coordinator is not None:
                await approval_coordinator.close()
            self.command_hook_sessions.clear()
            await self.hooks.close()
            await self.workspace_runtime.close()
            await self.execution.close()
            await self.service_runtime.close()
        except BaseException as error:
            observe_exception("runtime.close.failed", error)
            raise
        else:
            observe("runtime.close.complete")

    async def stop_anim(
        self,
        kind: ActivityStatusKind | None = None,
        *,
        settle: bool = True
    ) -> None:
        """停止指定类型的活动动画。"""
        if self.frontend.runtime.active:
            if kind == "wait":
                self.frontend.runtime.finish_turn_wait()
            await self.frontend.runtime.end_activity_status(
                kind,
                settle=settle,
            )
            return None
        await self.anim_manager.stop()

    async def freeze_anim(self, kind: ActivityStatusKind) -> None:
        """冻结指定活动动画并等待后续展示接管。"""
        if self.frontend.runtime.active:
            await self.frontend.runtime.freeze_activity_status(kind)
            return None
        await self.anim_manager.stop()

    async def start_anim(self) -> None:
        """启动模型响应等待动画。"""
        if not self.animate:
            return None
        if self.frontend.runtime.active:
            await self.frontend.runtime.begin_wait_status()
        return None

    async def start_upload_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动附件上传动画，并复用统一动画管理器避免冲突。"""
        if not self.animate:
            return None
        if self.frontend.runtime.active:
            await self.frontend.runtime.begin_upload_status(snapshot)
        return None

    async def start_inbuild_startup_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动内置运行时启动状态动画。"""
        if not self.animate:
            return None
        if self.frontend.runtime.active:
            await self.frontend.runtime.begin_inbuild_status(snapshot)
        return None

    async def start_external_mcp_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动外部 MCP 启动状态动画。"""
        if not self.animate:
            return None
        if self.frontend.runtime.active:
            await self.frontend.runtime.begin_external_mcp_status(snapshot)
        return None

    async def start_compact_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动对话压缩状态动画。"""
        if not self.animate:
            return None
        if self.frontend.runtime.active:
            await self.frontend.runtime.begin_compact_status(snapshot)
        return None

if __name__ == '__main__':
    pass
