# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import time
import typing
import asyncio
import sqlite3
import contextlib
from pathlib import Path
from engine.manage import ServerManage
from engine.animation import AsyncAnimManager
from engine.ports import terminate_port_process
from engine.errors import AppError
from mind_core.preference import Preferences
from mind_core.config_session import ConfigSession
from mind_core.agent_config import AgentSettings
from mind_core.feature_config import FeatureSettings
from mind_core.permissions import (
    PermissionSettings,
    resolve_permissions
)
from mind_core.hooks import (
    HookDefinitionConfig,
    SessionEndReason
)
from mind_nova.identifiers import short_uid
from mind_nova.events import EventReportPool
from .reporting import RunReport
from engine.observability import (
    observe,
    observe_exception
)
from .attach import Attach
from .runtime.support.calling import (
    calling as run_calling,
    run_turn_lifecycle as run_turn_lifecycle_wrapper
)
from .runtime.mcp.keepalive import run_keepalive
from .runtime.mcp.external import ExternalMcpRuntime
from .runtime.support.conversation import (
    ConversationState,
    ConversationTurn
)
from .runtime.mcp.tool_runtime import (
    CompositeToolRuntime,
    ToolRuntime
)
from .runtime.tools.mode_policy import ToolFilterMode
from .client_tools import (
    ClientToolRegistry,
    default_registry as default_client_tool_registry
)
from .native_coding import NativeCoding
from .native_coding.exec.user_shell import UserShellExecution
from .approval.coordinator import ApprovalCoordinator
from .runtime.subagents.runtime import SubagentRuntime
from .runtime.subagents.graph import AgentGraphStore
from .frontend.contracts import (
    ActivityStatusKind,
    Frontend
)
from .runtime.design import TerminalDesign
from .runtime.hooks.registry import HookRegistry
from .runtime.hooks.scope import (
    HookExecutionContext,
    HookExecutionScope
)
from .runtime.hooks.session import SessionLifecycleGateway
from .runtime.hooks.tool import CommandHookSessionStore
from .runtime.hooks.catalog import (
    HookCatalogSnapshot,
    HookCatalogStaleError
)
from .history import (
    ConversationHistoryStore,
    HISTORY_LIMIT,
    normalize_workspace
)
from .history.ids import valid_session_ids
from .history.transcript import (
    ConversationTranscriptStore,
    TranscriptEntry
)
from .mcp.contracts import McpSessionLike

SessionResult = typing.TypeVar("SessionResult")
CleanupResult = typing.TypeVar("CleanupResult")


def _normalize_tool_profile(value: str) -> ToolFilterMode:
    """校验并返回服务工具配置。"""
    if value not in {"app", "api"}:
        raise ValueError(f"Invalid Helix tool profile: {value}")
    return value


if typing.TYPE_CHECKING:
    from .runtime.turns.result import RunResult
    from .runtime.mcp.service_runtime import ServiceRuntimeContext
    from .runtime.turns.executor import TurnExecution
    from .subscription.runtime import AgentRuntime
    from server import ConfigServiceRuntime


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

        self.pref: Preferences               = kwargs["pref"]
        self.config_session: ConfigSession   = kwargs["config_session"]
        self.permissions: PermissionSettings = kwargs["permissions"]
        self.frontend: Frontend              = kwargs["frontend"]

        self.hook_registry: HookRegistry = (
            kwargs.get("hook_registry") or HookRegistry()
        )
        self.hook_startup_warnings = tuple(
            kwargs.get("hook_startup_warnings") or ()
        )

        self.hook_status = kwargs.get("hook_status")

        self.command_hook_sessions = CommandHookSessionStore()

        self.pref_refreshed_at: float    = time.monotonic()
        self.pref_refresh_ttl_sec: float = 1.0

        self.task_event: asyncio.Event = asyncio.Event()

        self.anim_manager: AsyncAnimManager = kwargs.get("anim_manager") or AsyncAnimManager()

        self.animate: bool = bool(kwargs.get("animate", True))

        self.design: TerminalDesign | None = kwargs.get("design")

        self.conversation: ConversationState         = ConversationState()
        self.history_store: ConversationHistoryStore = ConversationHistoryStore()

        self.transcripts: ConversationTranscriptStore = (
            kwargs.get("transcript_store") or ConversationTranscriptStore()
        )
        self.event_reports: EventReportPool = (
            kwargs.get("event_report_pool") or EventReportPool()
        )

        self._conversation_lifecycle_id: int = 0

        self.session_lifecycle = SessionLifecycleGateway(
            scope_factory=self.hook_scope,
            cleanup_session=self.hook_registry.cleanup_session,
        )

        self.report: RunReport = kwargs.get("report") or RunReport(self.src_total_place)

        self.attach: Attach = Attach()

        self.approval_coordinator = ApprovalCoordinator(
            self.frontend.interaction
        )

        self.features: FeatureSettings = (
            kwargs.get("feature_settings") or FeatureSettings()
        )

        self.native_coding: NativeCoding = NativeCoding(root=self.history_workspace)
        self.user_shell: UserShellExecution = self.native_coding.user_shell

        self.subagents: SubagentRuntime = (
            kwargs.get("subagent_runtime")
            or SubagentRuntime(
                self,
                enabled=self.features.subagents,
                settings=kwargs.get("agent_settings") or AgentSettings(),
                transcript_path_for=self.transcripts.path_for_session,
                session_cleanup=self._close_repl_session,
                graph_store=(
                    kwargs.get("agent_graph_store")
                    or AgentGraphStore(
                        ttl_ms=self.history_store.ttl_ms,
                        max_items=self.history_store.max_items,
                    )
                ),
            )
        )

        self._native_coding_close_tasks: set[asyncio.Task[None]] = set()

        self.server_manager: typing.Optional[ServerManage]       = None
        self.keepalive_stop: typing.Optional[asyncio.Event]      = None
        self.keepalive_task: typing.Optional[asyncio.Task[None]] = None

        self.service_runtime_context: typing.Optional["ServiceRuntimeContext"] = None
        self.service_exec_env: typing.Optional[dict[str, typing.Any]]          = None

        self._service_start_task: asyncio.Task[bool] | None = None
        self._service_start_lock: asyncio.Lock              = asyncio.Lock()

        self.config_service: ConfigServiceRuntime | None = None

        self.external_mcp: typing.Optional[ExternalMcpRuntime] = None

        self.subscription_runtime: AgentRuntime | None = None

        self.client_tools: ClientToolRegistry = self._build_client_tools()

        self.tool_runtime: ToolRuntime = CompositeToolRuntime(self)

        self.exit_code: int = 0

        self.service_mcp_linked: bool                    = False
        self.service_tool_profile: ToolFilterMode | None = None

        self.stop_runtime_on_exit: bool = False

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
    def keepalive_task_done(task: asyncio.Task[None]) -> None:
        """回收后台保活任务异常，避免事件循环输出未取回异常。"""
        if task.cancelled():
            return None

        try:
            error = task.exception()
        except asyncio.CancelledError:
            return None

        if error is not None:
            observe_exception("keepalive.task.failed", error, level="WARNING")

    @staticmethod
    async def await_cleanup(awaitable: typing.Awaitable[CleanupResult]) -> CleanupResult:
        """在取消态下也等待清理逻辑执行完成。"""
        task = asyncio.ensure_future(awaitable)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
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
        resolution       = self.config_session.resolve(workspace=target_workspace)

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

    def _native_coding_close_done(self, task: asyncio.Task[None]) -> None:
        """回收工作区切换时启动的进程清理任务。"""
        self._native_coding_close_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def _build_client_tools(self) -> ClientToolRegistry:
        """按当前工作区构建客户端工具注册表。"""
        return default_client_tool_registry(
            self.native_coding,
            execution_root=self.history_workspace,
            subagent_runtime=self.subagents,
            approval_coordinator=self.approval_coordinator,
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

    def bind_server_manager(self, server_manager: ServerManage) -> None:
        """绑定本地后台服务管理器。"""
        self.server_manager = server_manager

    def bind_service_runtime_context(self, context: "ServiceRuntimeContext") -> None:
        """绑定服务运行时准备上下文。"""
        self.service_runtime_context = context

    def require_service_runtime_context(self) -> "ServiceRuntimeContext":
        """返回已绑定的服务运行时上下文，未绑定时抛出错误。"""
        if self.service_runtime_context is None:
            raise AppError("Service runtime context is not bound")
        return self.service_runtime_context

    def link_service_mcp(
        self,
        exec_env: typing.Optional[dict[str, typing.Any]] = None,
        *,
        tool_profile: ToolFilterMode = "app"
    ) -> None:
        """把本地服务 MCP 挂入当前工具会话。"""
        normalized = _normalize_tool_profile(tool_profile)

        self.service_mcp_linked   = True
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

            previous_native_coding = self.native_coding
            self.history_workspace = normalized

            self.command_hook_sessions.clear()

            self.native_coding = NativeCoding(root=self.history_workspace)
            self.user_shell: UserShellExecution = self.native_coding.user_shell
            self.client_tools  = self._build_client_tools()

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None:
                task = loop.create_task(
                    previous_native_coding.close(),
                    name="coding workspace close",
                )
                self._native_coding_close_tasks.add(task)
                task.add_done_callback(self._native_coding_close_done)

            observe("workspace.changed", workspace=self.history_workspace)

        return self.history_workspace

    def unlink_service_mcp(self) -> None:
        """从当前工具会话移除本地服务 MCP，不停止后台进程。"""
        was_linked = self.service_mcp_linked

        self.service_mcp_linked   = False
        self.service_tool_profile = None
        self.service_exec_env     = None

        if was_linked:
            observe("helix.unlinked")

    def is_service_mcp_linked(self) -> bool:
        """判断当前工具会话是否挂载本地服务 MCP。"""
        return bool(self.service_mcp_linked)

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

    def start_keepalive_supervisor(self) -> None:
        """启动应用生命周期内的本地后台服务保活任务。"""
        if self.keepalive_task and not self.keepalive_task.done():
            return None

        self.keepalive_stop = asyncio.Event()

        self.keepalive_task = asyncio.create_task(
            run_keepalive(
                self.keepalive_stop,
                server_manager=self.server_manager
            ),
            name="local service keepalive"
        )
        self.keepalive_task.add_done_callback(self.keepalive_task_done)
        observe("keepalive.started")

    def start_subscription_listener(self) -> "AgentRuntime":
        """启动或复用当前进程的远端请求监听器。"""
        from .subscription.runtime import AgentRuntime

        runtime = self.subscription_runtime
        if runtime is None:
            runtime = AgentRuntime(self)
            self.subscription_runtime = runtime
        runtime.start_background()
        return runtime

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
        resolution       = self.config_session.resolve(workspace=target_workspace)

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
        }, ensure_effective={
            ("sandbox_mode",): settings.sandbox_mode,
            ("approval_policy",): settings.approval_policy,
            ("approvals_reviewer",): settings.approvals_reviewer,
        })
        effective = resolve_permissions(effective_config, interactive=True)
        self.permissions = effective
        return effective

    async def _close_repl_session(self, session_id: str) -> None:
        """关闭指定执行会话持有的 JavaScript Kernel。"""
        await self.native_coding.close_js_repl_session(session_id)

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
                await self.native_coding.close_js_repl_session(snapshot.thread.sid)

        with contextlib.suppress(Exception):
            await self.native_coding.close_js_repl_session(sid)

        self.command_hook_sessions.clear_root(sid)

        await self.session_lifecycle.end(
            self._conversation_lifecycle_id,
            self._session_hook_context(cid=cid, sid=sid),
            reason=reason,
            transcript_path=transcript_path,
            last_assistant_message=self.last_assistant_reply_snapshot(),
            before_dispatch=record_session_end,
        )
        await self.event_reports.close_session(cid, sid)

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

    async def run_service_runtime_startup(
        self,
        operation: typing.Callable[
            [],
            typing.Coroutine[typing.Any, typing.Any, bool],
        ]
    ) -> bool:
        """复用正在执行的本地服务准备任务。"""
        async with self._service_start_lock:
            task = self._service_start_task
            if task is None:
                task = asyncio.create_task(
                    operation(),
                    name="service runtime startup",
                )
                self._service_start_task = task

        try:
            return bool(await asyncio.shield(task))
        finally:
            if task.done():
                async with self._service_start_lock:
                    if self._service_start_task is task:
                        self._service_start_task = None

    async def cancel_service_runtime_startup(self) -> None:
        """取消并回收尚未完成的本地服务准备任务。"""
        async with self._service_start_lock:
            task = self._service_start_task
            self._service_start_task = None

        if task is None:
            return None
        if not task.done():
            task.cancel()

        await asyncio.gather(task, return_exceptions=True)

    async def start_config_service(self) -> None:
        """启动应用生命周期内的配置服务。"""
        if self.config_service is None:
            from server import ConfigServiceRuntime

            self.config_service = ConfigServiceRuntime(
                self.config_session,
                log_level=self.level,
            )
        await self.config_service.start()
        observe("config_service.started")

    async def stop_config_service(self) -> None:
        """停止应用生命周期内的配置服务。"""
        config_service = self.config_service
        self.config_service = None

        if config_service is None:
            return None

        await config_service.stop()
        observe("config_service.stopped")

    async def pause_subscription_listener(self) -> None:
        """停止远端请求传输并保留当前进程的收件箱。"""
        runtime = self.subscription_runtime
        if runtime is not None:
            await runtime.stop()

    async def stop_subscription_listener(self) -> None:
        """停止并释放当前进程的远端请求监听器。"""
        runtime = self.subscription_runtime
        self.subscription_runtime = None
        if runtime is not None:
            runtime.bind_inbox_changed(None)
            await runtime.shutdown()

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

    async def start_external_mcp_runtime(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False
    ) -> None:
        """启动应用生命周期级外部 MCP 运行时。"""
        if self.external_mcp is None:
            self.external_mcp = ExternalMcpRuntime(self)

        await self.external_mcp.start(
            include_disabled=include_disabled,
            defer_activity_stop=defer_activity_stop,
        )

    async def restart_external_mcp_runtime(
        self,
        *,
        include_disabled: bool = False,
        defer_activity_stop: bool = False
    ) -> None:
        """重启应用生命周期级外部 MCP 运行时。"""
        if self.external_mcp is None:
            self.external_mcp = ExternalMcpRuntime(self)

        await self.external_mcp.restart(
            include_disabled=include_disabled,
            defer_activity_stop=defer_activity_stop,
        )

    async def stop_external_mcp_runtime(self) -> None:
        """停止应用生命周期级外部 MCP 运行时。"""
        runtime = self.external_mcp

        self.external_mcp = None

        if runtime is not None:
            await self.await_cleanup(runtime.stop())

    async def stop_keepalive_supervisor(self) -> None:
        """停止应用生命周期内的本地后台服务保活任务。"""
        was_running = self.keepalive_stop is not None or self.keepalive_task is not None
        if self.keepalive_stop is not None:
            self.keepalive_stop.set()

        task = self.keepalive_task

        self.keepalive_task = None
        self.keepalive_stop = None

        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if was_running:
            observe("keepalive.stopped")

    async def close_runtime_resources(self) -> None:
        """关闭主控制器持有的运行时资源，并按退出策略处理本地后台进程。"""
        observe("runtime.close.start")
        try:
            await self.stop_subscription_listener()
            await self.cancel_service_runtime_startup()

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
            await self.event_reports.close()

            with contextlib.suppress(Exception):
                await self.native_coding.close()

            close_tasks = tuple(self._native_coding_close_tasks)
            self._native_coding_close_tasks.clear()
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)

            await self.stop_external_mcp_runtime()
            await self.stop_config_service()
            await self.stop_keepalive_supervisor()

            server_manager      = self.server_manager
            self.server_manager = None

            if server_manager is not None:
                try:
                    await server_manager.close()
                finally:
                    if self.stop_runtime_on_exit:
                        with contextlib.suppress(Exception):
                            await terminate_port_process(server_manager.port)
        except BaseException as error:
            observe_exception("runtime.close.failed", error)
            raise
        else:
            observe("runtime.close.complete")
        finally:
            self.report.close()

    async def reboot_runtime(self) -> None:
        """重启已绑定的后台进程，并在完成后恢复保活任务。"""
        server_manager = self.server_manager
        if server_manager is None:
            raise AppError("Server manager is not bound")

        observe("helix.restart.start")
        await self.stop_keepalive_supervisor()

        try:
            await server_manager.restart()
            ready = await server_manager.wait_until_ready(10.0, 0.3)
        finally:
            self.start_keepalive_supervisor()

        if not ready:
            raise AppError("Server not ready after reboot")

        observe("helix.restart.complete")

    async def stop_service_runtime(self) -> None:
        """停止已绑定的后台进程，并关闭对应保活任务。"""
        if self.server_manager is None:
            raise AppError("Server manager is not bound")

        observe("helix.stop.start")

        self.unlink_service_mcp()
        await self.stop_keepalive_supervisor()
        await terminate_port_process(self.server_manager.port)
        observe("helix.stop.complete")

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
                McpSessionLike,
                list[dict[str, typing.Any]],
            ],
            typing.Awaitable[SessionResult],
        ],
        before_user_flow: typing.Optional[typing.Callable[[], typing.Any]] = None
    ) -> SessionResult:
        """通过工具运行时建立会话并执行回调。"""
        return await self.tool_runtime.with_session(
            pref_config,
            function,
            before_user_flow=before_user_flow
        )

    async def run_turn_lifecycle(
        self,
        runner: typing.Callable[..., typing.Awaitable["RunResult"]],
        **kwargs
    ) -> "RunResult":
        """单轮执行生命周期入口。"""
        return await run_turn_lifecycle_wrapper(self, runner, **kwargs)

    async def calling(
        self,
        pref_config: typing.Optional[dict[str, typing.Any]] = None,
        *,
        message: str,
        **kwargs
    ) -> "RunResult":
        """调用入口：统一委托运行时模块执行单次请求。"""
        return await run_calling(
            self,
            pref_config=pref_config,
            message=message,
            **kwargs
        )

    async def stream_turn(
        self,
        session: McpSessionLike,
        pref_config: dict[str, typing.Any],
        tools: list[dict[str, typing.Any]],
        *_,
        turn_execution: "TurnExecution",
        **kwargs
    ) -> "RunResult":
        """流式执行入口。"""
        from .runtime.turns.stream import stream_turn as run_stream_turn

        return await run_stream_turn(
            self,
            session,
            pref_config,
            tools,
            turn_execution=turn_execution,
            **kwargs
        )


if __name__ == '__main__':
    pass
