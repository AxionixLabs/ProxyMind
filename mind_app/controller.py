# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import time
import typing
import asyncio
import sqlite3
import contextlib
from pathlib import Path
from infrastructure.platform.animation import AsyncAnimManager
from infrastructure.errors import AppError
from infrastructure.config.preferences import Preferences
from infrastructure.config.paths import ApplicationLayout
from infrastructure.config.session import ConfigSession
from agent.application.config.settings import (
    AgentSettings,
    FeatureSettings,
)
from agent.application.turns.context import TurnContext
from agent.application.turns.foreground import (
    ApplicationTurnForegroundLifecycle,
    FrontendTurnAnimation,
)
from agent.domain.policies import (
    PermissionSettings,
    resolve_permissions
)
from agent.domain.hooks import (
    HookDefinitionConfig,
    SessionEndReason
)
from agent.harness.mcp.owner import McpRuntimeOwner
from agent.application.hooks.context import HookExecutionContext
from protocol.schema.identifiers import (
    short_uid,
    valid_session_ids,
)
from observability.reporting import RunReport
from observability import (
    observe,
    observe_exception
)
from infrastructure.services.runtime_owner import ServiceRuntimeOwner
from protocol.client.reports import EventReportRuntimeOwner
from agent.harness.sessions.conversation import (
    ConversationState,
    ConversationTurn,
)
from agent.domain.tool_policy import ToolFilterMode
from infrastructure.mcp.local_tool_factory import (
    build_builtin_tool_registry,
    build_client_tool_registry,
)
from agent.stores.approvals.permissions import PermissionGrantStore
from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.stores.approvals.ledger import ApprovalCallLedger
from agent.harness.agents.runtime import SubagentRuntime
from .runtime.turns.subagent_adapter import (
    ControllerSubagentExecution,
    ControllerSubagentTurnRunner,
)
from .runtime.turns.session_context import (
    ControllerTurnSessionContext,
    ControllerTurnSessionState,
)
from .runtime.turns.execution_runtime import ControllerTurnExecutionRuntime
from .runtime.turns.root_session import ControllerRootTurnSession
from .runtime.turns.executor import resolve_turn_hook_scope
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
    HookRegistryPort,
    OutputSessionFactory,
    ProtocolCommandClient,
)
from agent.harness.hooks.scope import HookExecutionScope
from .runtime.hooks.session import SessionLifecycleGateway
from .runtime.hooks.tool import CommandHookSessionStore
from agent.application.hooks.catalog import (
    HookCatalogSnapshot,
    HookCatalogStaleError
)
from agent.stores.sessions import (
    ConversationHistoryStore,
    HISTORY_LIMIT,
    normalize_workspace
)
from infrastructure.persistence.transcripts import (
    ConversationTranscriptStore,
)
from agent.stores.transcripts import TranscriptEntry
from agent.ports import (
    McpRuntime,
    McpSessionPort,
    SubscriptionHost,
    SubscriptionRuntime,
    BeforeToolSession,
    ExternalToolGroupPort,
    ToolRegistryPort,
    ToolRuntimePort,
    ToolRuntimeSources,
)

SessionResult = typing.TypeVar("SessionResult")
CleanupResult = typing.TypeVar("CleanupResult")


def _unconfigured_mcp_runtime() -> McpRuntime:
    """返回明确配置错误，禁止控制器隐式构造具体 MCP 实现。"""
    raise RuntimeError("MCP runtime factory is required")


def _unconfigured_subscription_runtime(_host: SubscriptionHost) -> SubscriptionRuntime:
    """返回明确配置错误，禁止入口隐式构造订阅实现。"""
    raise RuntimeError("subscription runtime factory is required")


def _normalize_tool_profile(value: str) -> ToolFilterMode:
    """校验并返回服务工具配置。"""
    if value not in {"app", "api"}:
        raise ValueError(f"Invalid Helix tool profile: {value}")
    return value


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

        hook_registry = kwargs.get("hook_registry")
        if not isinstance(hook_registry, HookRegistryPort):
            raise TypeError("hook registry is required")
        self.hook_registry: HookRegistryPort = hook_registry
        self.hook_startup_warnings = tuple(
            kwargs.get("hook_startup_warnings") or ()
        )

        self.hook_status = kwargs.get("hook_status")

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
        self.turn_session_context = ControllerTurnSessionContext(self)

        self.conversation: ConversationState = ConversationState()
        self.turn_session_state = ControllerTurnSessionState(self)
        self.history_store: ConversationHistoryStore = (
            kwargs.get("history_store")
            or ConversationHistoryStore(mind_history_db_path())
        )

        self.transcripts: ConversationTranscriptStore = (
            kwargs.get("transcript_store") or ConversationTranscriptStore()
        )
        self.event_reporting = EventReportRuntimeOwner(
            pool=kwargs.get("event_report_pool"),
        )
        self.turn_execution_runtime = ControllerTurnExecutionRuntime(self)
        self.subagent_turn_runner = ControllerSubagentTurnRunner(
            self.turn_execution_runtime,
        )
        self.subagent_cleanup = self
        subagent_session_factory: OutputSessionFactory = kwargs[
            "subagent_session_factory"
        ]
        self.subagent_execution = ControllerSubagentExecution(
            self.turn_execution_runtime,
            model_capability=self.runtime_services.model_capability,
            protocol_client=(
                self.runtime_services.model_capability
                if isinstance(
                    self.runtime_services.model_capability,
                    ProtocolCommandClient,
                )
                else None
            ),
            effect_journal_factory=self.runtime_services.create_effect_journal,
            session_factory=subagent_session_factory,
        )
        self.root_turn_session = ControllerRootTurnSession(self)
        self._conversation_lifecycle_id: int = 0

        self.session_lifecycle = SessionLifecycleGateway(
            scope_factory=self.hook_scope,
            cleanup_session=self.hook_registry.cleanup_session,
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

        subagent_runtime = kwargs.get("subagent_runtime")
        if subagent_runtime is None:
            skills_provider_factory = getattr(
                self.runtime_services,
                "create_skills_provider",
                None,
            )
            skills_provider = (
                skills_provider_factory(self.config_session.load)
                if callable(skills_provider_factory)
                else None
            )
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
                transcript_factory=self.transcripts.writer,
                cleanup=self,
                patch_preview=self.workspace_runtime.coding.preview_patch,
                skills_provider=skills_provider,
                transcript_path_for=self.transcripts.path_for_session,
                transcript_entries_for=(
                    lambda path: self.transcripts.reader(path).read()
                ),
                session_cleanup=self._close_repl_session,
                graph_store=(
                    kwargs.get("agent_graph_store")
                    or AgentGraphStore(
                        agent_graph_db_path(),
                        ttl_ms=self.history_store.ttl_ms,
                        max_items=self.history_store.max_items,
                    )
                ),
            )
        self.subagents: SubagentRuntime = subagent_runtime

        self.service_exec_env: typing.Optional[dict[str, typing.Any]] = None

        mcp_runtime_builder = getattr(
            self.runtime_services,
            "create_mcp_runtime",
            None,
        )
        if callable(mcp_runtime_builder):
            self.external_mcp = McpRuntimeOwner(
                runtime_factory=lambda: mcp_runtime_builder(self),
            )
        else:
            self.external_mcp = McpRuntimeOwner(
                runtime_factory=_unconfigured_mcp_runtime,
            )
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

        self.client_tools: ToolRegistryPort = self._build_client_tools()
        self.builtin_tools: ToolRegistryPort = self._build_builtin_tools()
        self.service_mcp_linked: bool = False
        self.service_tool_profile: ToolFilterMode | None = None

        tool_runtime_builder = self.runtime_services.create_tool_runtime
        if not callable(tool_runtime_builder):
            raise TypeError("tool runtime factory is required")
        self.tool_runtime: ToolRuntimePort = tool_runtime_builder(
            ToolRuntimeSources(
                client_registry=lambda: self.client_tools,
                builtin_registry=lambda: self.builtin_tools,
                external_group=self._current_external_tool_group,
                service_linked=self.is_service_mcp_linked,
            )
        )

        self.exit_code: int = 0

        self.last_assistant_reply: str = ""

        observe(
            "controller.ready",
            run_id=getattr(self.report, "run_id", None),
            workspace=self.history_workspace,
            animate=self.animate,
            client_tools=len(self.client_tools.list_tools().tools),
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

    def _session_hook_context(
        self,
        *,
        cid: str,
        sid: str
    ) -> HookExecutionContext:
        """构建根会话生命周期事件使用的固定上下文。"""
        pref_config = self.pref.to_config()

        primary = (
            pref_config.get("primary")
            if isinstance(pref_config, dict)
            else None
        )

        model = (
            str(primary.get("model") or "").strip()
            if isinstance(primary, dict)
            else ""
        )

        return HookExecutionContext(
            session_id=sid,
            root_session_id=sid,
            conversation_id=cid,
            turn_id="",
            cwd=self.history_workspace,
            model=model,
            source="session",
            sandbox_mode=self.permissions.sandbox_mode,
            permission_mode=self.permissions.approval_policy,
            agent_id="root",
            agent_type="root",
            agent_depth=0,
        )

    def _touch_history_session(
        self,
        metadata: dict[str, str],
        *,
        title: str = "",
        source: str
    ) -> None:
        """把 cid/sid 写入本地 history SQLite。"""
        try:
            self.history_store.touch_session(
                cid=metadata["cid"],
                sid=metadata["sid"],
                title=title,
                workspace=self.history_workspace,
                source=source,
                branch=metadata.get("branch", ""),
                status=metadata.get("status", "active"),
            )
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            observe_exception(
                "history.write.failed",
                exc,
                level="WARNING",
                source=source,
            )

    def _resolve_hook_state_target(
        self,
        hook_key: str,
        *,
        expected_content_hash: str,
        workspace: Path | None
    ) -> tuple[Path, HookDefinitionConfig]:
        """解析并校验允许修改用户状态的 Hook。"""
        target_workspace = self._hook_workspace(workspace)
        resolution = self.config_session.resolve(workspace=target_workspace)

        definition = next(
            (
                item
                for item in resolution.hooks
                if item.key == hook_key
            ),
            None,
        )
        if definition is None:
            raise HookCatalogStaleError(
                f"hook is unavailable: {hook_key}"
            )

        expected_hash = str(expected_content_hash or "").strip().lower()
        if definition.content_hash != expected_hash:
            raise HookCatalogStaleError(
                f"hook content changed: {hook_key}"
            )
        return target_workspace, definition

    def _hook_workspace(self, workspace: Path | None) -> Path:
        """返回 Hook 查询使用的绝对工作区路径。"""
        target = workspace or Path(self.history_workspace)
        return Path(target).expanduser().resolve()

    def _build_client_tools(self) -> ToolRegistryPort:
        """按当前工作区构建客户端工具注册表。"""
        return build_client_tool_registry(
            self.workspace_runtime.coding,
            image_reader=self.workspace_runtime.image_reader,
            execution_policy=self.workspace_runtime.execution_policy,
            subagent_runtime=self.subagents,
            approval_coordinator=self.approval_coordinator,
            features=self.features,
        )

    def _build_builtin_tools(self) -> ToolRegistryPort:
        """按当前能力开关构建核心内置工具注册表。"""
        return build_builtin_tool_registry(
            approval_coordinator=self.approval_coordinator,
            permission_grants=self.permission_grants,
            features=self.features,
        )

    def recent_conversation_sessions(
        self,
        *,
        workspace: str | Path | None = None,
        sources: typing.Collection[str] | None = None,
        status: str | None = None,
        limit: int = HISTORY_LIMIT
    ) -> list[dict[str, typing.Any]]:
        """返回可恢复的本地会话游标。"""
        try:
            records = self.history_store.list_sessions(
                workspace=workspace,
                sources=sources,
                status=status,
                limit=limit,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            observe_exception("history.list.failed", exc, level="WARNING")
            return []

        return [
            record for record in records
            if valid_session_ids(record.get("cid"), record.get("sid"))
        ]

    def find_conversation_session(
        self,
        session_id: str,
        *,
        workspace: str | Path | None = None,
        sources: typing.Collection[str] | None = None,
        status: str | None = None
    ) -> dict[str, typing.Any] | None:
        """按会话标识返回可恢复的本地会话游标。"""
        try:
            record = self.history_store.find_session(
                session_id,
                workspace=workspace,
                sources=sources,
                status=status,
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            observe_exception("history.find.failed", exc, level="WARNING")
            return None

        if record is None or not valid_session_ids(
            record.get("cid"),
            record.get("sid"),
        ):
            return None
        return record

    def read_conversation_transcript(
        self,
        session_id: str
    ) -> tuple[TranscriptEntry, ...]:
        """读取指定会话已经持久化的结构化事件。"""
        path = self.transcripts.existing_path_for_session(session_id)
        if not path:
            return ()
        return self.transcripts.reader(path).read()

    def prepare_conversation_fork(
        self,
        cid: str,
        sid: str,
        before_turn_id: str = ""
    ) -> str:
        """持久化并返回当前源会话的稳定分支请求标识。"""
        candidate = f"fork_{short_uid(20)}"
        try:
            return self.history_store.get_or_create_fork_request(
                cid=cid,
                sid=sid,
                request_id=candidate,
                before_turn_id=before_turn_id,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            observe_exception("conversation.fork.prepare_failed", error)
            raise AppError("Unable to persist the conversation fork request.") from error

    def clear_conversation_fork(
        self,
        cid: str,
        sid: str,
        request_id: str,
        before_turn_id: str = ""
    ) -> None:
        """清除已完成或不可重试的本地分支请求。"""
        try:
            self.history_store.clear_fork_request(
                cid=cid,
                sid=sid,
                request_id=request_id,
                before_turn_id=before_turn_id,
            )
        except (OSError, sqlite3.Error, ValueError) as error:
            observe_exception(
                "conversation.fork.clear_failed",
                error,
                level="WARNING",
            )

    def link_service_mcp(
        self,
        exec_env: typing.Optional[dict[str, typing.Any]] = None,
        *,
        tool_profile: ToolFilterMode = "app"
    ) -> None:
        """把本地服务 MCP 挂入当前工具会话。"""
        normalized = _normalize_tool_profile(tool_profile)

        self.service_mcp_linked = True
        self.service_tool_profile = normalized

        self.service_exec_env = (
            copy.deepcopy(exec_env)
            if isinstance(exec_env, dict)
            else None
        )
        observe(
            "helix.linked",
            tool_profile=normalized,
            exec_env=bool(self.service_exec_env),
        )

    def set_service_tool_profile(self, tool_profile: ToolFilterMode) -> None:
        """切换已经挂载的服务工具配置。"""
        if not self.service_mcp_linked:
            raise AppError("Helix MCP is not linked")

        normalized = _normalize_tool_profile(tool_profile)
        self.service_tool_profile = normalized
        observe("helix.tool_profile.changed", tool_profile=normalized)

    def set_history_workspace(self, workspace: typing.Any) -> str:
        """更新 history 使用的真实工作区根目录。"""
        normalized = normalize_workspace(workspace)

        if normalized and normalized != self.history_workspace:
            self.workspace_runtime.replace(normalized)
            self.history_workspace = normalized

            self.command_hook_sessions.clear()
            self.client_tools  = self._build_client_tools()

            observe("workspace.changed", workspace=self.history_workspace)

        return self.history_workspace

    def unlink_service_mcp(self) -> None:
        """从当前工具会话移除本地服务 MCP，不停止后台进程。"""
        was_linked = self.service_mcp_linked

        self.service_mcp_linked = False
        self.service_tool_profile = None
        self.service_exec_env = None

        if was_linked:
            observe("helix.unlinked")

    def is_service_mcp_linked(self) -> bool:
        """判断当前工具会话是否挂载本地服务 MCP。"""
        return bool(self.service_mcp_linked)

    def _current_external_tool_group(self) -> ExternalToolGroupPort | None:
        """返回当前外部 MCP runtime 已发布的工具组。"""
        runtime = self.external_mcp.current
        return runtime.group if runtime is not None else None

    def tool_profile_for_turn(self) -> ToolFilterMode | None:
        """返回当前模型请求使用的服务工具配置。"""
        if not self.service_mcp_linked:
            return None
        if self.service_tool_profile is None:
            raise AppError("Helix tool profile is not selected")
        return self.service_tool_profile

    def service_exec_env_snapshot(self) -> dict[str, typing.Any] | None:
        """返回本地服务运行时环境快照。"""
        if not isinstance(self.service_exec_env, dict):
            return None
        return copy.deepcopy(self.service_exec_env)

    def remember_last_assistant_reply(self, text: str) -> None:
        """记录最近一次完整模型回复原文。"""
        value = str(text or "").strip()
        if value:
            self.last_assistant_reply = value

    def last_assistant_reply_snapshot(self) -> str:
        """返回最近一次完整模型回复原文。"""
        return self.last_assistant_reply

    def hook_scope(
        self,
        context: HookExecutionContext
    ) -> HookExecutionScope:
        """为指定执行上下文构建固定的 Hook 作用域。"""
        workspace  = self._hook_workspace(Path(context.cwd))

        resolution = self.config_session.resolve(
            workspace=workspace
        )

        return HookExecutionScope(
            context=context,
            dispatcher=self.hook_registry.build(
                resolution.hooks,
                hook_states=resolution.hook_states,
                warnings=resolution.hook_warnings,
                status_port=getattr(self, "hook_status", None),
            ),
        )

    def turn_hook_scope(self, context: TurnContext) -> HookExecutionScope:
        """为 SubagentRuntime 提供绑定当前轮次的 Hook 作用域。"""
        return resolve_turn_hook_scope(self, context)

    def inspect_hooks(
        self,
        *,
        workspace: Path | None = None
    ) -> HookCatalogSnapshot:
        """返回指定工作区的实时 Hook 管理快照。"""
        target_workspace = self._hook_workspace(workspace)

        resolution = self.config_session.resolve(
            workspace=target_workspace
        )

        return self.hook_registry.inspect(
            resolution.hooks,
            hook_states=resolution.hook_states,
            warnings=resolution.hook_warnings,
            workspace=target_workspace,
        )

    def trust_hook(
        self,
        hook_key: str,
        *,
        expected_content_hash: str,
        workspace: Path | None = None
    ) -> HookCatalogSnapshot:
        """信任指定 Hook 的当前内容。"""
        return self.trust_hooks(
            ((hook_key, expected_content_hash),),
            workspace=workspace,
        )

    def trust_hooks(
        self,
        hooks: typing.Iterable[tuple[str, str]],
        *,
        workspace: Path | None = None
    ) -> HookCatalogSnapshot:
        """校验并批量信任多个 Hook 的当前内容。"""
        target_workspace = self._hook_workspace(workspace)
        resolution = self.config_session.resolve(workspace=target_workspace)

        definitions: dict[str, HookDefinitionConfig] = {}

        for hook_definition in resolution.hooks:
            definitions[hook_definition.key] = hook_definition
        updates: dict[tuple[str, ...], str] = {}

        for hook_key, expected_content_hash in hooks:
            hook_definition = definitions.get(hook_key)
            if hook_definition is None:
                raise HookCatalogStaleError(
                    f"hook is unavailable: {hook_key}"
                )
            expected_hash = str(expected_content_hash or "").strip().lower()
            if hook_definition.content_hash != expected_hash:
                raise HookCatalogStaleError(
                    f"hook content changed: {hook_key}"
                )
            if hook_definition.trust_policy != "content_hash":
                raise ValueError(
                    f"{hook_definition.trust_policy} hook trust cannot be changed"
                )
            updates[(
                "hooks",
                "state",
                hook_definition.key,
                "trusted_hash",
            )] = hook_definition.content_hash

        if updates:
            self.config_session.update_user(updates)
        return self.inspect_hooks(workspace=target_workspace)

    def set_hook_enabled(
        self,
        hook_key: str,
        *,
        expected_content_hash: str,
        enabled: bool,
        workspace: Path | None = None
    ) -> HookCatalogSnapshot:
        """更新指定 Hook 的独立启用状态。"""
        target_workspace, definition = self._resolve_hook_state_target(
            hook_key,
            expected_content_hash=expected_content_hash,
            workspace=workspace,
        )
        if definition.trust_policy == "managed":
            raise ValueError(
                "managed hook enabled state cannot be changed"
            )

        self.config_session.update_user({
            (
                "hooks",
                "state",
                definition.key,
                "enabled",
            ): bool(enabled),
        })

        return self.inspect_hooks(workspace=target_workspace)

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

    async def _close_repl_session(self, session_id: str) -> None:
        """关闭指定执行会话持有的 JavaScript Kernel。"""
        await self.workspace_runtime.coding.close_js_repl_session(session_id)

    async def begin_conversation_turn(
        self,
        cid: typing.Optional[str] = None,
        sid: typing.Optional[str] = None,
        *,
        title: str = "",
        source: str = "begin"
    ) -> ConversationTurn:
        """为新轮次初始化或续用当前会话标识。"""
        external_cid = str(cid or "").strip()
        external_sid = str(sid or "").strip()
        if external_cid or external_sid:
            if not valid_session_ids(external_cid, external_sid):
                raise ValueError("valid cid and sid are required")
            if (
                self.conversation.cid
                and self.conversation.sid
                and (
                    external_cid != self.conversation.cid
                    or external_sid != self.conversation.sid
                )
            ):
                await self.end_conversation(reason="switch")
                self._conversation_lifecycle_id += 1
                self.last_assistant_reply = ""

        turn = self.conversation.begin_turn(
            cid=cid,
            sid=sid,
            start_reason=source,
        )
        metadata = turn.metadata()

        self._touch_history_session(metadata, title=title, source=source)

        observe(
            "conversation.begin",
            cid=metadata.get("cid"),
            sid=metadata.get("sid"),
            source=source,
            session_started=turn.session_started,
            start_reason=turn.start_reason,
        )

        return turn

    async def reset_conversation(
        self,
        *,
        reason: str = "manual",
        source: str = "reset",
        title: str = ""
    ) -> dict[str, str]:
        """开始一个新的模型对话。"""
        await self.end_conversation(reason="reset")
        metadata = self.conversation.reset(reason=reason)
        self._conversation_lifecycle_id += 1
        self.last_assistant_reply = ""

        self._touch_history_session(metadata, title=title, source=source)

        observe(
            "conversation.reset",
            cid=metadata.get("cid"),
            sid=metadata.get("sid"),
            reason=reason,
            source=source,
        )

        return metadata

    async def resume_conversation(
        self,
        record: dict[str, typing.Any],
        *,
        source: str = "resume"
    ) -> typing.Optional[dict[str, str]]:
        """把当前会话绑定到 history 中选中的 cid/sid。"""
        cid = str(record.get("cid") or "").strip()
        sid = str(record.get("sid") or "").strip()

        if not valid_session_ids(cid, sid):
            observe(
                "history.resume.skipped",
                level="WARNING",
                reason="invalid_cursor",
                cid=cid,
                sid=sid,
            )
            return None

        metadata = await self.bind_conversation(cid, sid, source=source)
        if metadata is not None:
            observe("history.resumed", cid=cid, sid=sid)
        return metadata

    async def bind_conversation(
        self,
        cid: str,
        sid: str,
        *,
        source: str = "bind"
    ) -> typing.Optional[dict[str, str]]:
        """把当前运行绑定到一组已存在的远端会话标识。"""
        if not valid_session_ids(cid, sid):
            observe(
                "conversation.bind.skipped",
                level="WARNING",
                reason="invalid_cursor",
                cid=cid,
                sid=sid,
                source=source,
            )
            return None

        if self.conversation.cid == cid and self.conversation.sid == sid:
            self.conversation.session_bound = True
            self.conversation.fork_source_available = True
            metadata = self.conversation.snapshot()
            self._touch_history_session(metadata, source=source)
            observe("conversation.reused", cid=cid, sid=sid, source=source)
            return metadata

        await self.end_conversation(reason="switch")
        self.conversation = ConversationState(
            cid=cid,
            sid=sid,
            start_reason=source,
            fork_source_available=True,
        )
        self._conversation_lifecycle_id += 1
        self.last_assistant_reply = ""

        metadata = self.conversation.snapshot()

        self._touch_history_session(metadata, source=source)

        observe("conversation.bound", cid=cid, sid=sid, source=source)

        return metadata

    async def end_conversation(self, *, reason: SessionEndReason) -> None:
        """结束当前已绑定的根会话生命周期。"""
        conversation = self.conversation

        cid = str(conversation.cid or "").strip()
        sid = str(conversation.sid or "").strip()

        if not conversation.session_bound or not valid_session_ids(cid, sid):
            return None

        transcript_path = self.transcripts.path_for_session(sid)

        transcript = self.transcripts.writer(
            transcript_path,
            session_id=sid,
        )

        def record_session_end() -> None:
            """在结束 Hook 前写入根会话终态。"""
            transcript.open()
            try:
                transcript.append(
                    "session.ended",
                    actor="system",
                    payload={"reason": reason},
                )
            finally:
                transcript.close()

        subagent_snapshots = await self.subagents.shutdown_root(sid)

        for snapshot in subagent_snapshots:
            await self.hook_registry.cleanup_session(snapshot.thread.sid)
            with contextlib.suppress(Exception):
                await self.workspace_runtime.coding.close_js_repl_session(
                    snapshot.thread.sid
                )

        with contextlib.suppress(Exception):
            await self.workspace_runtime.coding.close_js_repl_session(sid)

        self.command_hook_sessions.clear_root(sid)

        await self.session_lifecycle.end(
            self._conversation_lifecycle_id,
            self._session_hook_context(cid=cid, sid=sid),
            reason=reason,
            transcript_path=transcript_path,
            last_assistant_message=self.last_assistant_reply_snapshot(),
            before_dispatch=record_session_end,
        )
        await self.event_reporting.close_session(cid, sid)

    async def archive_conversation(self) -> dict[str, typing.Any]:
        """将当前根会话迁移到 archived 集合并结束其生命周期。"""
        conversation = self.conversation

        cid = str(conversation.cid or "").strip()
        sid = str(conversation.sid or "").strip()

        if not conversation.session_bound or not valid_session_ids(cid, sid):
            raise LookupError("conversation session is not started")

        archived = self.history_store.archive_session(cid=cid, sid=sid)
        try:
            await self.end_conversation(reason="archive")
        except BaseException:
            try:
                self.history_store.unarchive_session(cid=cid, sid=sid)
            except Exception as rollback_error:
                observe_exception(
                    "history.archive.rollback.failed",
                    rollback_error,
                    level="ERROR",
                    cid=cid,
                    sid=sid,
                )
            raise
        return archived

    async def archive_conversation_session(
        self,
        cid: str,
        sid: str
    ) -> dict[str, typing.Any]:
        """把指定的非当前会话迁移到 archived 集合。"""
        if not valid_session_ids(cid, sid):
            raise ValueError("valid cid and sid are required")
        if (str(self.conversation.cid), str(self.conversation.sid)) == (
            str(cid),
            str(sid),
        ):
            raise ValueError(
                "Use /archive to archive the current session and exit."
            )
        return self.history_store.archive_session(cid=cid, sid=sid)

    async def unarchive_conversation(
        self,
        cid: str,
        sid: str
    ) -> dict[str, typing.Any]:
        """把指定 archived 会话迁移回 active 集合。"""
        return self.history_store.unarchive_session(cid=cid, sid=sid)

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
            await self.hook_registry.close()
            await self.event_reporting.close()

            await self.workspace_runtime.close()

            await self.external_mcp.close()
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

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        function: typing.Callable[
            [
                McpSessionPort,
                list[dict[str, typing.Any]],
            ],
            typing.Awaitable[SessionResult],
        ],
        before_user_flow: BeforeToolSession | None = None,
    ) -> SessionResult:
        """通过工具运行时建立会话并执行回调。"""
        return await self.tool_runtime.with_session(
            pref_config,
            function,
            before_user_flow=before_user_flow
        )


if __name__ == '__main__':
    pass
