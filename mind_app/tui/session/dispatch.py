# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import enum
import typing
import asyncio
from pathlib import Path
from dataclasses import (
    dataclass,
    replace
)
from engine.file_assist import FileAssist
from mind_app.runtime.mcp.service_runtime import service_runtime_asset_missing
from mind_app.frontend import ApplicationView
from mind_app.history import INTERACTIVE_HISTORY_SOURCES
from mind_core.config_store import ConfigStoreError
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan
)
from server import config_service_base_url
from ..core.models import FragmentBlock
from ..core.interrupt import InterruptDisposition
from ..core.runtime import TuiRuntime
from ..core.styles import (
    BODY_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    command_result_block,
    failure_text_block,
    fragment_block,
    text_block
)
from ..contracts.resume import (
    ResumeRow,
    ResumeSessionStatus
)
from ..features.context import ignored_tui_input
from ..features.agents import manage_agents
from ..features.conversation import (
    ForkLiveStatus,
    compact_current_conversation,
    confirm_archive_session,
    copy_last_assistant_reply,
    fork_current_conversation,
    render_compact_failure,
    render_compact_interrupted,
    render_compact_result,
    render_fork_failure,
    render_fork_interrupted,
    render_fork_result
)
from ..features.diff import show_workspace_diff
from ..features.helix import (
    choose_helix_tool_profile,
    confirm_runtime_download,
    download_service_runtime,
    open_helix_home,
    render_helix_command_failure,
    render_helix_download_result,
    render_helix_home_failure,
    render_helix_home_result,
    render_helix_interrupted,
    render_helix_mode_result,
    render_helix_notice,
    render_helix_stop_failure,
    render_helix_stop_result,
    stop_helix_runtime,
    unlink_helix_runtime
)
from ..features.history import (
    HistoryResumePreviewLoader,
    HistoryResumeTranscriptLoader,
    choose_history_session,
    load_history_transcript
)
from ..features.hooks import manage_hooks
from ..features.listener import (
    choose_listener_action,
    parse_listener_command,
    render_listener_status
)
from ..features.mailbox import TuiMailboxFeature
from ..features.mcp import (
    McpAction,
    choose_mcp_action,
    parse_mcp_command,
    render_mcp_status
)
from ..features.model import (
    choose_model_effort,
    choose_provider,
    exchange_pref_value,
    model_changed_status_block,
    persist_primary_pref,
    provider_changed_status_block,
    render_model_effort_status,
    save_active_provider
)
from ..features.permissions import (
    choose_permissions_mode,
    render_permissions_status
)
from ..features.processes import (
    append_exec_stream_snapshot,
    manage_exec_sessions,
    stop_all_exec_sessions
)
from ..features.shell import run_shell_escape
from ..features.skills import choose_skill
from ..features.tools import print_available_tools
from ..prompting.commands import (
    StreamCommandPolicy,
    TUI_COMMANDS,
    TuiCommandSpec,
    command_spec,
    matches_command,
    resolve_tui_command,
    slash_command_notice_message,
    stream_command_policy
)
from .barriers import TuiForegroundTasks
from .state import TuiSessionState

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ..runtime.ports import ProcessRuntimePort, SkillRuntimePort

MODEL_COMMAND_PATTERN = re.compile(
    rf"^\s*{re.escape(command_spec('model').command)}(?:\s+(.+))?\s*$",
    re.IGNORECASE
)

_BARRIER_STREAM_POLICIES: typing.Final[frozenset[StreamCommandPolicy]] = (
    frozenset[StreamCommandPolicy]({
        "background_barrier",
        "interactive_panel",
        "settings_settlement",
    })
)


@dataclass(frozen=True, slots=True)
class StreamLocalAction(object):
    """描述不阻塞当前轮次或下一轮输入的本地异步动作。"""
    key: str
    name: str
    factory: typing.Callable[
        [],
        typing.Coroutine[typing.Any, typing.Any, None],
    ]


@dataclass(frozen=True, slots=True)
class StreamBarrierAction(object):
    """描述不阻塞当前轮次但必须先于下一轮完成的动作。"""
    start: typing.Callable[[], bool]


StreamResolvedAction = StreamLocalAction | StreamBarrierAction


@dataclass(frozen=True, slots=True)
class StreamCommandRequest(object):
    """保存一次已授权流式命令的规范输入。"""
    value: str
    normalized: str
    cancel_turn: typing.Callable[[], InterruptDisposition]


StreamActionResolver = typing.Callable[
    [StreamCommandRequest],
    StreamResolvedAction,
]


class DispatchAction(enum.Enum):
    """描述一项输入完成命令分派后的下一步。"""
    HANDLED = "handled"
    MODEL_TURN = "model_turn"
    EXIT = "exit"


class TuiCommandDispatcher(object):
    """分派 TUI shell、斜杠命令和普通模型输入。"""

    def __init__(
        self,
        mind: "Mind",
        runtime: TuiRuntime,
        state: TuiSessionState,
        foreground_tasks: TuiForegroundTasks
    ) -> None:
        self.mind    = mind
        self.runtime = runtime
        self.state   = state

        self.foreground_tasks = foreground_tasks
        self.application      = mind.frontend.application
        self.mailbox          = TuiMailboxFeature(runtime, mind)

        self._local_tasks: dict[str, asyncio.Task[None]] = {}
        self._stream_action_resolvers = self._build_stream_action_resolvers()
        self._validate_stream_action_resolvers()

    def _build_stream_action_resolvers(
        self,
    ) -> dict[str, StreamActionResolver]:
        """构建由命令目录键驱动的流式动作注册表。"""
        return {
            "permissions": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Model permissions",
                    lambda: self._update_permissions(present_on_cancel=False),
                )
            ),
            "model": self._resolve_stream_model_action,
            "provider": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Model provider",
                    lambda: self._switch_provider(present_on_cancel=False),
                )
            ),
            "effort": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Reasoning effort",
                    lambda: self._choose_effort(present_on_cancel=False),
                )
            ),
            "helix_mode": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Helix tool mode",
                    lambda: self._choose_helix_mode(
                        present_on_cancel=False,
                        wait_for_download=False,
                    ),
                )
            ),
            "preferences": lambda _request: StreamLocalAction(
                key="preferences",
                name="tui preferences browser",
                factory=self._open_preferences,
            ),
            "tools": lambda _request: StreamLocalAction(
                key="tools",
                name="tui tools snapshot",
                factory=self._show_tools,
            ),
            "hooks": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Hooks menu",
                    lambda: manage_hooks(self.runtime, self.mind),
                )
            ),
            "agent": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Agents menu",
                    lambda: manage_agents(self.runtime, self.mind),
                )
            ),
            "listen": lambda request: self._resolve_stream_listener_action(
                request.normalized
            ),
            "mailbox": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Mailbox menu",
                    self.mailbox.open,
                )
            ),
            "diff": lambda _request: self._diff_local_action(),
            "copy": lambda _request: StreamLocalAction(
                key="copy",
                name="tui copy assistant response",
                factory=lambda: copy_last_assistant_reply(self.mind),
            ),
            "ps": lambda _request: StreamLocalAction(
                key="ps",
                name="tui background terminals snapshot",
                factory=lambda: append_exec_stream_snapshot(
                    typing.cast(
                        "ProcessRuntimePort",
                        typing.cast(object, self.runtime),
                    ),
                    self.mind,
                ),
            ),
            "stop": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Stop background terminals",
                    lambda: stop_all_exec_sessions(
                        typing.cast(
                            "ProcessRuntimePort",
                            typing.cast(object, self.runtime),
                        ),
                        self.mind,
                    ),
                )
            ),
            "mcp": lambda request: self._resolve_stream_mcp_action(
                request.normalized
            ),
            "helix_link": lambda request: StreamBarrierAction(
                lambda: self.foreground_tasks.handle_stream_command(
                    request.value,
                    request.cancel_turn,
                )
            ),
            "helix_home": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Helix Home preparation",
                    lambda: self._open_helix_home(wait_for_completion=False),
                )
            ),
            "skills": lambda _request: StreamBarrierAction(
                lambda: self.foreground_tasks.start(
                    "Skills menu",
                    self._choose_skill,
                )
            ),
        }

    def _validate_stream_action_resolvers(self) -> None:
        """校验命令目录与会话层流式动作注册表完全一致。"""
        declared = {
            command.key
            for command in TUI_COMMANDS
            if command.requires_stream_action
        }
        registered = set(self._stream_action_resolvers)
        if declared == registered:
            self._validate_stream_action_policies()
            return None

        missing    = ", ".join(sorted(declared - registered)) or "none"
        unexpected = ", ".join(sorted(registered - declared)) or "none"
        raise RuntimeError(
            "Invalid stream action registry: "
            f"missing={missing}; unexpected={unexpected}"
        )

    def _validate_stream_action_policies(self) -> None:
        """校验一级命令和子命令解析动作与声明策略一致。"""
        for command in TUI_COMMANDS:
            cases = (
                (command.command, command.stream_policy),
                *(
                    (
                        f"{command.command} {subcommand}",
                        command.policy_during_task(subcommand),
                    )
                    for subcommand in command.subcommands
                ),
            )
            resolver = self._stream_action_resolvers.get(command.key)

            for value, policy in cases:
                if policy in {"reject", "interrupt"}:
                    continue
                if resolver is None:
                    raise RuntimeError(
                        f"No stream action registered for {value}"
                    )

                action = resolver(StreamCommandRequest(
                    value=value,
                    normalized=value.casefold(),
                    cancel_turn=lambda: InterruptDisposition.IGNORED,
                ))
                if policy == "local_snapshot":
                    valid = isinstance(action, StreamLocalAction)
                else:
                    valid = isinstance(action, StreamBarrierAction)
                if not valid:
                    raise RuntimeError(
                        f"Stream action policy mismatch for {value}: {policy}"
                    )

    def _resolve_stream_model_action(
        self,
        request: StreamCommandRequest,
    ) -> StreamResolvedAction:
        """解析模型查询或更新动作。"""
        matcher = MODEL_COMMAND_PATTERN.match(request.value)
        if matcher is None:
            raise RuntimeError("Registered model stream action received invalid input")
        return StreamBarrierAction(lambda: self.foreground_tasks.start(
            "Model selection",
            lambda: self._save_model(matcher),
        ))

    def _present(
        self,
        renderable: FragmentBlock | StyledBlock | None = None,
        *,
        view_type: str = "tui.output"
    ) -> None:
        """发送命令分派产生的正文展示。"""
        resolved_type = view_type

        if renderable is None and view_type == "tui.output":
            resolved_type = "tui.gap"
        self.application.emit(ApplicationView(
            type=resolved_type,
            renderable=renderable,
        ))

    def _finish_conversation_fork(self, status: ForkLiveStatus) -> None:
        """在普通会话分支成功后清理旧草稿并展示结果。"""
        if status.succeeded:
            self._clear_prompt_draft()
        render_fork_result(self.mind, status)

    def _clear_prompt_draft(self) -> None:
        """清除当前会话尚未提交的结构化草稿。"""
        self.state.clear_pending_prompt_extras()
        self.mind.attach.clear_pending_attachments()

    def _start_local_action(
        self,
        action: StreamLocalAction,
    ) -> bool:
        """启动不参与下一轮屏障的去重本地动作。"""
        previous = self._local_tasks.get(action.key)
        if previous is not None and not previous.done():
            return True

        task = self.runtime.start_background_task(
            action.factory(),
            name=action.name,
        )
        self._local_tasks[action.key] = task
        task.add_done_callback(
            lambda completed: self._forget_local_action(
                action.key,
                completed,
            )
        )
        return True

    def _diff_local_action(self) -> StreamLocalAction:
        """返回普通状态和流式状态共用的差异查询动作。"""
        return StreamLocalAction(
            key="diff",
            name="tui git diff",
            factory=self._workspace_diff_coroutine,
        )

    def _workspace_diff_coroutine(
        self,
    ) -> typing.Coroutine[typing.Any, typing.Any, None]:
        """在创建后台任务前固定工作目录并返回差异查询协程。"""
        cwd = Path(self.mind.history_workspace).resolve()
        return show_workspace_diff(
            self.runtime,
            self.mind,
            cwd=cwd,
        )

    def _forget_local_action(
        self,
        key: str,
        task: asyncio.Task[None],
    ) -> None:
        """回收已经完成的运行中本地动作。"""
        if self._local_tasks.get(key) is task:
            self._local_tasks.pop(key, None)

    def handle_stream_command(
        self,
        value: str,
        cancel_turn: typing.Callable[[], InterruptDisposition]
    ) -> bool:
        """分派模型流式期间可执行的本地命令。"""
        normalized = str(value or "").strip().casefold()
        policy     = stream_command_policy(normalized)
        command    = resolve_tui_command(normalized)

        if policy in {None, "reject"} or command is None:
            return False

        if policy == "interrupt":
            return self.foreground_tasks.handle_stream_command(
                value,
                cancel_turn,
            )

        action = self._resolve_stream_action(
            command,
            StreamCommandRequest(
                value=value,
                normalized=normalized,
                cancel_turn=cancel_turn,
            ),
        )

        if policy == "local_snapshot":
            if not isinstance(action, StreamLocalAction):
                raise RuntimeError(
                    f"Stream command {command.command} requires a local action"
                )
            return self._start_local_action(action)

        if policy in _BARRIER_STREAM_POLICIES:
            if not isinstance(action, StreamBarrierAction):
                raise RuntimeError(
                    f"Stream command {command.command} requires a barrier action"
                )
            return action.start()

        raise RuntimeError(
            f"Unsupported stream command policy for {command.command}: {policy}"
        )

    def _resolve_stream_action(
        self,
        command: TuiCommandSpec,
        request: StreamCommandRequest,
    ) -> StreamResolvedAction:
        """把已授权命令解析为与声明策略匹配的运行期动作。"""
        resolver = self._stream_action_resolvers.get(command.key)
        if resolver is None:
            raise RuntimeError(
                f"No stream action registered for {command.command}"
            )
        return resolver(request)

    async def _choose_effort(self, *, present_on_cancel: bool = True) -> None:
        """选择并持久化模型推理强度。"""
        await self.state.refresh_preferences(self.mind, ttl_sec=0.0)
        primary = self.state.pref_config.get("primary")
        current = primary if isinstance(primary, dict) else {}

        selected = await choose_model_effort(
            self.runtime,
            current.get("reasoning_effort"),
        )

        if selected is None:
            if present_on_cancel:
                self._present()
            return None

        saved = await persist_primary_pref(
            self.mind,
            command_name="model-effort",
            field_name="reasoning_effort",
            field_value=selected,
        )
        if saved is None:
            return None

        effort = saved.get("reasoning_effort") or selected

        self.state.merge_primary(saved, overrides={"reasoning_effort": effort})
        self.state.apply_prompt_context(self.runtime)
        render_model_effort_status(self.application, effort)

    async def _save_model(self, matcher: re.Match[str]) -> None:
        """解析并持久化模型命令。"""
        if not matcher.group(1):
            await self.state.refresh_preferences(self.mind, ttl_sec=0.0)
            primary = self.state.pref_config.get("primary")
            current = primary if isinstance(primary, dict) else {}
            self._present(command_result_block(
                "/model",
                TextSpan(str(current.get("model") or "(not configured)"), BRIGHT_STYLE),
            ))
            self._present()
            return None

        model_value = await exchange_pref_value(
            self.application,
            matcher,
            pref_command="model",
        )
        if model_value is None:
            return None

        saved = await persist_primary_pref(
            self.mind,
            command_name="model",
            field_name="model",
            field_value=model_value,
        )
        if saved is None:
            return None

        model = str(saved.get("model") or model_value)

        self.state.merge_primary(saved, overrides={"model": model})
        self.state.apply_prompt_context(self.runtime)
        self._present(model_changed_status_block(
            model,
            saved.get("reasoning_effort"),
        ))
        self._present()

    async def _switch_provider(self, *, present_on_cancel: bool = True) -> None:
        """选择并持久化当前 Provider Profile。"""
        try:
            selected = await choose_provider(
                self.runtime,
                self.mind.config_session,
            )
            if selected is None:
                if present_on_cancel:
                    self._present()
                return None
            saved = await save_active_provider(
                self.mind.config_session,
                selected,
            )
            await self.mind.refresh_pref_if_stale(ttl_sec=0.0)
        except (OSError, TypeError, ValueError) as error:
            self._present(command_result_block(
                "/provider",
                TextSpan(f"Failed: {error}", FAILURE_STYLE),
            ))
            self._present()
            return None

        primary = saved.get("primary") if isinstance(saved, dict) else {}
        current = primary if isinstance(primary, dict) else {}

        self.state.merge_primary(current)
        self.state.apply_prompt_context(self.runtime)

        self._present(provider_changed_status_block(
            current.get("name") or selected,
            current.get("kind"),
            current.get("model"),
        ))
        self._present()

    async def _update_permissions(
        self,
        *,
        present_on_cancel: bool = True,
    ) -> None:
        """选择、持久化并同步当前会话权限。"""
        selected = await choose_permissions_mode(
            self.runtime,
            self.state.permissions,
        )
        if selected is None:
            if present_on_cancel:
                self._present()
            return None

        try:
            effective = self.mind.apply_permissions(selected)
        except (ConfigStoreError, TypeError, ValueError) as failure:
            self._present(failure_text_block(
                f"Failed to update permissions: {failure}",
            ))
            self._present()
            return None

        self.state.permissions = effective
        self.state.apply_prompt_context(self.runtime)
        render_permissions_status(self.application, effective)

    async def _show_tools(self) -> None:
        """刷新偏好快照并展示当前可用工具。"""
        await self.state.refresh_preferences(self.mind, ttl_sec=0.0)
        await print_available_tools(
            self.mind,
            pref_config=self.state.pref_config,
        )

    async def _open_preferences(self) -> None:
        """在系统浏览器中打开偏好配置页面。"""
        preferences_url = f"{config_service_base_url()}/pref"
        try:
            await FileAssist.open_url(preferences_url)
        except Exception as failure:
            self._present(failure_text_block(
                f"Failed to open browser for {preferences_url}: {failure}",
            ))
        else:
            self._present(fragment_block(
                TextSpan("• ", BODY_STYLE),
                TextSpan(
                    f"Opened {preferences_url} in your browser.",
                    BRIGHT_STYLE,
                ),
            ))
        self._present()

    async def _choose_skill(self) -> None:
        """打开当前运行时可用的 skill 选择面板。"""
        skill_runtime = typing.cast(
            "SkillRuntimePort",
            typing.cast(object, self.runtime),
        )
        await choose_skill(skill_runtime, self.mind.config_session)

    def _resolve_stream_listener_action(
        self,
        command: str,
    ) -> StreamResolvedAction:
        """解析活动轮次中的监听器查询、菜单或状态切换。"""
        is_listener, action = parse_listener_command(command)
        if not is_listener:
            raise RuntimeError(
                "Registered listener stream action received invalid input"
            )
        if action is None:
            return StreamBarrierAction(lambda: self.foreground_tasks.start(
                "Listener menu",
                self._choose_stream_listener_action,
            ))
        if action == "status":
            return StreamLocalAction(
                key="listen",
                name="tui listener status",
                factory=lambda: _run_immediate_stream_action(
                    lambda: render_listener_status(self.mind)
                ),
            )
        return StreamBarrierAction(
            lambda: self.foreground_tasks.start_listener(
                action,
                on_succeeded=self.mailbox.bind_listener,
            )
        )

    async def _choose_stream_listener_action(self) -> None:
        """选择并启动不会中断当前模型轮次的监听器操作。"""
        action = await choose_listener_action(self.runtime, self.mind)
        if action is None:
            return None
        self.foreground_tasks.start_listener(
            action,
            on_succeeded=self.mailbox.bind_listener,
        )

    def _resolve_stream_mcp_action(
        self,
        command: str,
    ) -> StreamResolvedAction:
        """解析活动轮次中不会破坏当前工具会话的 MCP 操作。"""
        is_mcp, action = parse_mcp_command(command)
        if not is_mcp or action is None:
            raise RuntimeError("Registered MCP stream action received invalid input")
        if action == "status":
            return StreamLocalAction(
                key="mcp",
                name="tui external mcp status",
                factory=lambda: _run_immediate_stream_action(
                    lambda: render_mcp_status(self.mind)
                ),
            )
        return StreamBarrierAction(
            lambda: self.foreground_tasks.handle_stream_command(
                command,
                lambda: False,
            )
        )

    async def _dispatch_mcp(self, mcp_action: McpAction | None) -> None:
        """执行即时 MCP 操作或建立可取消前台任务。"""
        action = mcp_action
        if action is None:
            action = await choose_mcp_action(self.runtime, self.mind)

        if action is None:
            self._present()
            return None

        if action == "status":
            render_mcp_status(self.mind)
            return None

        self.foreground_tasks.start_external_mcp(action)
        await self.foreground_tasks.wait()

    async def _resume_conversation(self) -> None:
        """选择并恢复最近的会话。"""
        records = self.mind.recent_conversation_sessions(
            workspace=self.mind.history_workspace,
            sources=INTERACTIVE_HISTORY_SOURCES,
        )
        selected = await choose_history_session(
            self.runtime,
            records,
            filter_workspace=self.mind.history_workspace,
            preview_loader=HistoryResumePreviewLoader(
                self.mind,
                terminal_capabilities=self.runtime.terminal_capabilities,
            ),
            transcript_loader=HistoryResumeTranscriptLoader(
                self.mind,
                terminal_capabilities=self.runtime.terminal_capabilities,
            ),
            archive_session=self._archive_resume_row,
            unarchive_session=self._unarchive_resume_row,
        )
        if selected is None:
            self._present()
            return None

        session_id = str(selected.get("sid") or "").strip()

        replay_blocks = await asyncio.to_thread(
            load_history_transcript,
            self.mind,
            session_id,
            terminal_width=self.runtime.terminal_width,
            hyperlinks=self.runtime.hyperlinks_enabled,
            terminal_capabilities=self.runtime.terminal_capabilities,
            record=selected,
        )

        resume_error: str | None = None
        try:
            resumed = await self.mind.resume_conversation(
                selected,
                source="tui:resume",
            )
        except Exception as error:
            resumed = None
            resume_error = str(error).strip() or type(error).__name__

        if resumed is None:
            target_label = str(selected.get("title") or session_id).strip()
            detail = resume_error or "invalid session cursor."
            self._present(failure_text_block(
                f"Failed to resume session from {target_label}: {detail}"
            ))
            self._present()
            return None

        self._clear_prompt_draft()
        self.runtime.replace_transcript(replay_blocks)

    async def _archive_resume_row(self, row: "ResumeRow") -> None:
        """归档 Resume picker 中的非当前会话。"""
        current = (self.mind.conversation.cid, self.mind.conversation.sid)
        if row.key == current:
            raise ValueError("Use /archive to archive the current session and exit.")
        await self.mind.archive_conversation_session(row.cid, row.sid)

    async def _unarchive_resume_row(self, row: "ResumeRow") -> "ResumeRow":
        """恢复 Resume picker 中的 archived 会话。"""
        await self.mind.unarchive_conversation(row.cid, row.sid)
        return replace(row, status=ResumeSessionStatus.ACTIVE)

    async def _download_missing_helix_runtime(
        self,
        command: str,
        *,
        wait_for_completion: bool = True,
        present_on_cancel: bool = True,
    ) -> bool:
        """发现缺失运行时时完成下载并结束当前命令。"""
        context = self.mind.require_service_runtime_context()
        if not service_runtime_asset_missing(context):
            return False

        if not await confirm_runtime_download(self.runtime, context):
            if present_on_cancel:
                self._present()
            return True

        self.foreground_tasks.start(
            "Helix runtime download",
            lambda: download_service_runtime(self.mind, context),
            activity_kind="download",
            on_succeeded=lambda _downloaded: render_helix_download_result(
                self.mind,
                command,
            ),
            on_failed=lambda error: render_helix_command_failure(
                self.mind,
                command,
                error,
            ),
            on_cancelled=lambda: render_helix_interrupted(
                self.mind,
                label="Helix download",
            ),
        )
        if wait_for_completion:
            await self.foreground_tasks.wait()
        return True

    async def _choose_helix_mode(
        self,
        *,
        present_on_cancel: bool = True,
        wait_for_download: bool = True,
    ) -> None:
        """选择并应用后续模型轮次使用的 Helix 工具过滤模式。"""
        if not self.mind.is_service_mcp_linked():
            render_helix_notice(
                self.mind,
                "Helix MCP is not connected",
            )
            return None
        if await self._download_missing_helix_runtime(
            "/helix-mode",
            wait_for_completion=wait_for_download,
            present_on_cancel=present_on_cancel,
        ):
            return None

        current = self.mind.tool_profile_for_turn()
        if current is None:
            render_helix_notice(
                self.mind,
                "Helix tool mode is unavailable",
            )
            return None

        selected = await choose_helix_tool_profile(self.runtime, current)
        if selected is None:
            if present_on_cancel:
                self._present()
            return None

        self.mind.set_service_tool_profile(selected)
        render_helix_mode_result(self.mind, selected)
        self.state.invalidate_workspace()

    def _finish_helix_home(self, home_url: str) -> None:
        """展示 Helix 首页打开结果并刷新工作区关联状态。"""
        render_helix_home_result(self.mind, home_url)
        self.state.invalidate_workspace()

    async def _open_helix_home(
        self,
        *,
        wait_for_completion: bool = True,
    ) -> None:
        """准备运行时并打开当前已连接的 Helix 首页。"""
        if not self.mind.is_service_mcp_linked():
            render_helix_notice(
                self.mind,
                "Helix MCP is not connected",
            )
            return None
        if await self._download_missing_helix_runtime(
            "/helix-home",
            wait_for_completion=wait_for_completion,
            present_on_cancel=wait_for_completion,
        ):
            return None

        self.foreground_tasks.start(
            "Helix Home",
            lambda: open_helix_home(self.mind),
            on_succeeded=self._finish_helix_home,
            on_failed=lambda error: render_helix_home_failure(
                self.mind,
                error,
            ),
            on_cancelled=lambda: render_helix_interrupted(
                self.mind,
                label="Helix Home",
            ),
        )
        if wait_for_completion:
            await self.foreground_tasks.wait()

    async def dispatch(self, prompt_text: str) -> DispatchAction:
        """处理一项输入并返回会话循环的下一步。"""
        slash_notice = slash_command_notice_message(prompt_text)
        if slash_notice:
            self._present(text_block(slash_notice, BODY_STYLE))
            return DispatchAction.HANDLED

        if ignored_tui_input(prompt_text):
            return DispatchAction.HANDLED

        if prompt_text.startswith("!"):
            self._present()
            if await run_shell_escape(self.runtime, self.mind, prompt_text):
                self._present()
                return DispatchAction.HANDLED

        command = prompt_text.strip().lower()

        if command.startswith("/"):
            self._present()

        if matches_command(command, "quit"):
            self.mind.task_event.set()
            return DispatchAction.EXIT

        if matches_command(command, "archive"):
            try:
                if not await confirm_archive_session(self.runtime):
                    return DispatchAction.HANDLED
                await self.mind.archive_conversation()
            except Exception as failure:
                message = str(failure).strip()
                if message == "conversation session is not started":
                    message = "A thread must start before it can be archived."
                else:
                    message = (
                        "Failed to archive current thread: "
                        f"{message or type(failure).__name__}"
                    )
                self._present(failure_text_block(
                    message,
                ))
                self._present()
                return DispatchAction.HANDLED
            self.mind.task_event.set()
            return DispatchAction.EXIT

        new_command = resolve_tui_command(command)
        if new_command is not None and new_command.key == "new":
            parts = prompt_text.strip().split(maxsplit=1)
            title = parts[1].strip() if len(parts) == 2 else ""
            reset_kwargs = {
                "reason": "command:/new",
                "source": "tui:new",
            }
            if title:
                reset_kwargs["title"] = title
            try:
                await self.mind.reset_conversation(**reset_kwargs)
            except Exception as failure:
                self._present(failure_text_block(
                    f"Failed to start a fresh session: {failure}",
                ))
                self._present()
                return DispatchAction.HANDLED
            self._clear_prompt_draft()
            self._present(fragment_block(
                TextSpan("• ", BODY_STYLE),
                TextSpan("New conversation", BRIGHT_STYLE),
            ))
            self._present()
            return DispatchAction.HANDLED

        if matches_command(command, "shutdown"):
            self.mind.stop_runtime_on_exit = True
            self.mind.task_event.set()
            self._present(fragment_block(
                TextSpan("• ", BODY_STYLE),
                TextSpan("Stopping backend runtime.", BRIGHT_STYLE),
            ))
            self._present()
            return DispatchAction.EXIT

        if matches_command(command, "permissions"):
            await self._update_permissions()
            return DispatchAction.HANDLED

        if matches_command(command, "tools"):
            await self._show_tools()
            return DispatchAction.HANDLED

        if matches_command(command, "hooks"):
            await manage_hooks(self.runtime, self.mind)
            return DispatchAction.HANDLED

        if matches_command(command, "agent"):
            await manage_agents(self.runtime, self.mind)
            return DispatchAction.HANDLED

        is_listener_command, listener_action = parse_listener_command(command)

        if is_listener_command:
            if listener_action is None:
                listener_action = await choose_listener_action(
                    self.runtime,
                    self.mind,
                )
                if listener_action is None:
                    return DispatchAction.HANDLED
            if listener_action == "status":
                render_listener_status(self.mind)
            else:
                self.foreground_tasks.start_listener(listener_action)
                await self.foreground_tasks.wait()
                self.mailbox.bind_listener()
            return DispatchAction.HANDLED

        if matches_command(command, "mailbox"):
            await self.mailbox.open()
            return DispatchAction.HANDLED

        if matches_command(command, "diff"):
            self._start_local_action(self._diff_local_action())
            return DispatchAction.HANDLED

        if matches_command(command, "copy"):
            await copy_last_assistant_reply(self.mind)
            return DispatchAction.HANDLED

        if matches_command(command, "skills"):
            await self._choose_skill()
            return DispatchAction.HANDLED

        if matches_command(command, "effort"):
            await self._choose_effort()
            return DispatchAction.HANDLED

        if matches_command(command, "provider"):
            await self._switch_provider()
            return DispatchAction.HANDLED

        if matches_command(command, "ps"):
            if await manage_exec_sessions(
                typing.cast(
                    "ProcessRuntimePort",
                    typing.cast(object, self.runtime),
                ),
                self.mind,
            ):
                self._present()
            return DispatchAction.HANDLED

        if matches_command(command, "stop"):
            await stop_all_exec_sessions(
                typing.cast(
                    "ProcessRuntimePort",
                    typing.cast(object, self.runtime),
                ),
                self.mind,
            )
            self._present()
            return DispatchAction.HANDLED

        if matcher := MODEL_COMMAND_PATTERN.match(prompt_text):
            await self._save_model(matcher)
            return DispatchAction.HANDLED

        if matches_command(command, "preferences"):
            await self._open_preferences()
            return DispatchAction.HANDLED

        if matches_command(command, "compact"):
            await self.state.refresh_preferences(self.mind, ttl_sec=0.0)
            self.foreground_tasks.start(
                "Context compaction",
                lambda: compact_current_conversation(
                    self.mind,
                    pref_config=self.state.pref_config,
                ),
                activity_kind="compact",
                on_succeeded=lambda status: render_compact_result(
                    self.mind,
                    status,
                ),
                on_failed=lambda error: render_compact_failure(
                    self.mind,
                    error,
                ),
                on_cancelled=lambda: render_compact_interrupted(self.mind),
            )
            await self.foreground_tasks.wait()
            return DispatchAction.HANDLED

        if matches_command(command, "fork"):
            self.foreground_tasks.start(
                "Conversation fork",
                lambda: fork_current_conversation(
                    self.mind,
                ),
                activity_kind="compact",
                on_succeeded=self._finish_conversation_fork,
                on_failed=lambda error: render_fork_failure(
                    self.mind,
                    error,
                ),
                on_cancelled=lambda: render_fork_interrupted(self.mind),
            )
            await self.foreground_tasks.wait()
            return DispatchAction.HANDLED

        if matches_command(command, "helix_link"):
            self.foreground_tasks.start_helix_link()
            await self.foreground_tasks.wait()
            self.state.invalidate_workspace()
            return DispatchAction.HANDLED

        if matches_command(command, "helix_mode"):
            await self._choose_helix_mode()
            return DispatchAction.HANDLED

        if matches_command(command, "helix_unlink"):
            unlink_helix_runtime(self.mind)
            self.state.invalidate_workspace()
            return DispatchAction.HANDLED

        if matches_command(command, "helix_home"):
            await self._open_helix_home()
            return DispatchAction.HANDLED

        if matches_command(command, "helix_stop"):
            if not self.mind.is_service_mcp_linked():
                render_helix_notice(
                    self.mind,
                    "Helix MCP is not connected",
                )
                return DispatchAction.HANDLED

            self.foreground_tasks.start(
                "Helix MCP stop",
                lambda: stop_helix_runtime(self.mind),
                activity_kind="operation",
                on_succeeded=lambda result: render_helix_stop_result(
                    self.mind,
                    result,
                ),
                on_failed=lambda error: render_helix_stop_failure(
                    self.mind,
                    error,
                ),
                on_cancelled=lambda: render_helix_interrupted(
                    self.mind,
                    label="Helix MCP stop",
                ),
            )
            await self.foreground_tasks.wait()
            self.state.invalidate_workspace()
            return DispatchAction.HANDLED

        is_mcp_command, mcp_action = parse_mcp_command(command)

        if is_mcp_command:
            await self._dispatch_mcp(mcp_action)
            return DispatchAction.HANDLED

        if matches_command(command, "resume"):
            await self._resume_conversation()
            return DispatchAction.HANDLED

        return DispatchAction.MODEL_TURN


async def _run_immediate_stream_action(callback: typing.Callable[[], None]) -> None:
    """在统一本地任务生命周期中执行同步命令动作。"""
    callback()


if __name__ == '__main__':
    pass
