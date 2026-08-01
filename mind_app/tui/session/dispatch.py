# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import enum
import typing
import asyncio
from engine.file_assist import FileAssist
from mind_app.frontend import ApplicationView
from mind_app.history import INTERACTIVE_HISTORY_SOURCES
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan
)
from mind_nova.modes import RunMode
from server import config_service_base_url
from ..core.models import FragmentBlock
from ..core.runtime import TuiRuntime
from ..core.styles import (
    BODY_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    command_result_block,
    text_block
)
from ..features.context import ignored_tui_input
from ..features.agents import manage_agents
from ..features.conversation import (
    ForkLiveStatus,
    compact_current_conversation,
    copy_last_assistant_reply,
    finish_compact_activity,
    finish_fork_activity,
    fork_current_conversation,
    render_compact_failure,
    render_compact_interrupted,
    render_compact_result,
    render_fork_failure,
    render_fork_interrupted,
    render_fork_result
)
from ..features.diff import print_current_apply_patch_diff
from ..features.helix import (
    finish_helix_activity,
    open_helix_home,
    render_helix_home_failure,
    render_helix_home_result,
    render_helix_interrupted,
    render_helix_stop_failure,
    render_helix_stop_result,
    stop_helix_runtime,
    unlink_helix_runtime
)
from ..features.history import (
    choose_history_session,
    load_history_transcript
)
from ..features.hooks import manage_hooks
from ..features.mcp import (
    McpAction,
    choose_mcp_action,
    parse_mcp_command,
    render_mcp_status
)
from ..features.mode import render_mode_status
from ..features.model import (
    choose_model_effort,
    exchange_pref_value,
    persist_primary_pref,
    render_model_effort_status
)
from ..features.permissions import (
    choose_permissions_mode,
    render_permissions_status
)
from ..features.processes import (
    append_exec_stream_snapshot,
    manage_exec_sessions
)
from ..features.shell import run_shell_escape
from ..features.skills import choose_skill
from ..features.tools import print_available_tools
from ..prompting.commands import (
    command_spec,
    is_unrecognized_slash_command,
    matches_command,
    unrecognized_slash_command_message
)
from .barriers import TuiForegroundTasks
from .state import TuiSessionState

if typing.TYPE_CHECKING:
    from ...controller import Mind

_MODE_BY_KEY: typing.Final[dict[str, RunMode]] = {
    "chat": "chat",
    "fast": "fast",
    "xtra": "xtra",
}

MODE_BY_COMMAND: dict[str, RunMode] = {
    name: mode
    for key, mode in _MODE_BY_KEY.items()
    for name in command_spec(key).names
}

MODEL_COMMAND_PATTERN = re.compile(
    rf"^\s*{re.escape(command_spec('model').command)}(?:\s+(.+))?\s*$",
    re.IGNORECASE
)


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
        foreground_tasks: TuiForegroundTasks,
    ) -> None:
        self.mind    = mind
        self.runtime = runtime
        self.state   = state

        self.foreground_tasks = foreground_tasks
        self.application      = mind.frontend.application

        self._process_snapshot_task: asyncio.Task[None] | None = None
        self._agent_menu_task: asyncio.Task[None] | None       = None

    async def dispatch(self, prompt_text: str) -> DispatchAction:
        """处理一项输入并返回会话循环的下一步。"""
        if ignored_tui_input(prompt_text):
            return DispatchAction.HANDLED

        if prompt_text.startswith("!"):
            self._present()
            if await run_shell_escape(self.runtime, self.mind, prompt_text):
                self._present()
                return DispatchAction.HANDLED

        command = prompt_text.strip().lower()

        if is_unrecognized_slash_command(prompt_text):
            self._present(text_block(
                unrecognized_slash_command_message(prompt_text),
                BODY_STYLE,
            ))
            return DispatchAction.HANDLED

        if command.startswith("/"):
            self._present()

        if matches_command(command, "quit"):
            self.mind.task_event.set()
            return DispatchAction.EXIT

        if matches_command(command, "new"):
            metadata = await self.mind.reset_conversation(
                reason="command:/new",
                source="tui:new",
            )
            self._clear_prompt_draft()
            self._present(command_result_block(
                "/new",
                TextSpan("New conversation", BRIGHT_STYLE),
                TextSpan(
                    f" · cid={metadata['cid']} sid={metadata['sid']}",
                    MUTED_STYLE,
                ),
            ))
            self._present()
            return DispatchAction.HANDLED

        if matches_command(command, "shutdown"):
            self.mind.stop_runtime_on_exit = True
            self.mind.task_event.set()
            self._present(command_result_block(
                "/shutdown",
                TextSpan("Stopping backend runtime", BRIGHT_STYLE),
            ))
            self._present()
            return DispatchAction.EXIT

        if matches_command(command, "permissions"):
            selected = await choose_permissions_mode(
                self.runtime,
                self.state.permissions,
            )
            if selected is None:
                self._present()
            else:
                self.state.permissions = selected
                self.state.apply_prompt_context(self.runtime)
                render_permissions_status(
                    self.application,
                    self.state.permissions,
                )
            return DispatchAction.HANDLED

        if matches_command(command, "tools"):
            await self.state.refresh_preferences(self.mind, ttl_sec=0.0)
            await print_available_tools(
                self.mind,
                run_mode=self.state.mode,
                pref_config=self.state.pref_config,
            )
            return DispatchAction.HANDLED

        if matches_command(command, "hooks"):
            await manage_hooks(self.runtime, self.mind)
            return DispatchAction.HANDLED

        if matches_command(command, "agent"):
            await manage_agents(self.runtime, self.mind)
            return DispatchAction.HANDLED

        if matches_command(command, "diff"):
            print_current_apply_patch_diff(self.mind)
            return DispatchAction.HANDLED

        if matches_command(command, "copy"):
            await copy_last_assistant_reply(self.mind)
            return DispatchAction.HANDLED

        if matches_command(command, "skills"):
            await choose_skill(self.runtime)
            return DispatchAction.HANDLED

        if matches_command(command, "effort"):
            await self._choose_effort()
            return DispatchAction.HANDLED

        if matches_command(command, "ps"):
            if await manage_exec_sessions(self.runtime, self.mind):
                self._present()
            return DispatchAction.HANDLED

        if matcher := MODEL_COMMAND_PATTERN.match(prompt_text):
            await self._save_model(matcher)
            return DispatchAction.HANDLED

        if matches_command(command, "preferences"):
            preferences_url = f"{config_service_base_url()}/pref"
            self._present(command_result_block(
                "/preferences",
                TextSpan(preferences_url, BRIGHT_STYLE),
            ))
            await FileAssist.open_url(preferences_url)
            self._present()
            return DispatchAction.HANDLED

        if matches_command(command, "compact"):
            await self.state.refresh_preferences(self.mind, ttl_sec=0.0)
            self.foreground_tasks.start(
                "Context compaction",
                lambda: compact_current_conversation(
                    self.mind,
                    run_mode=self.state.mode,
                    pref_config=self.state.pref_config,
                ),
                finish_activity=lambda: finish_compact_activity(self.mind),
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
                    run_mode=self.state.mode,
                ),
                finish_activity=lambda: finish_fork_activity(self.mind),
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

        if matches_command(command, "helix_unlink"):
            unlink_helix_runtime(self.mind)
            self.state.invalidate_workspace()
            return DispatchAction.HANDLED

        if matches_command(command, "helix_home"):
            self.foreground_tasks.start(
                "Helix Home",
                lambda: open_helix_home(self.mind),
                cancel_cleanup=self.mind.cancel_service_runtime_startup,
                finish_activity=lambda: finish_helix_activity(self.mind),
                on_succeeded=lambda home_url: render_helix_home_result(
                    self.mind,
                    home_url,
                ),
                on_failed=lambda error: render_helix_home_failure(
                    self.mind,
                    error,
                ),
                on_cancelled=lambda: render_helix_interrupted(
                    self.mind,
                    label="Helix Home",
                ),
            )
            await self.foreground_tasks.wait()
            self.state.invalidate_workspace()
            return DispatchAction.HANDLED

        if matches_command(command, "helix_stop"):
            self.foreground_tasks.start(
                "Helix MCP stop",
                lambda: stop_helix_runtime(self.mind),
                finish_activity=lambda: self.runtime.end_activity_status(
                    "operation",
                    settle=False,
                ),
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

        if command in MODE_BY_COMMAND:
            self.state.mode = MODE_BY_COMMAND[command]
            self.state.apply_prompt_context(self.runtime)
            render_mode_status(self.application, self.state.mode)
            return DispatchAction.HANDLED

        return DispatchAction.MODEL_TURN

    def handle_stream_command(
        self,
        value: str,
        cancel_turn: typing.Callable[[], bool]
    ) -> bool:
        """分派模型流式期间可执行的本地命令。"""
        command = str(value or "").strip().casefold()
        if matches_command(command, "ps"):
            self._start_process_snapshot()
            return True
        if matches_command(command, "agent"):
            self._start_agent_menu()
            return True

        return self.foreground_tasks.handle_stream_command(value, cancel_turn)

    def _start_process_snapshot(self) -> None:
        """启动不接管输入焦点的后台终端快照任务。"""
        previous = self._process_snapshot_task
        if previous is not None and not previous.done():
            previous.cancel()

        task = self.runtime.start_background_task(
            append_exec_stream_snapshot(self.runtime, self.mind),
            name="tui background terminals snapshot",
        )
        self._process_snapshot_task = task
        task.add_done_callback(self._forget_process_snapshot)

    def _forget_process_snapshot(self, task: asyncio.Task[None]) -> None:
        """回收已完成的后台终端快照任务。"""
        if self._process_snapshot_task is task:
            self._process_snapshot_task = None

    def _start_agent_menu(self) -> None:
        """在流式期间启动可交互的子执行线程菜单。"""
        previous = self._agent_menu_task
        if previous is not None and not previous.done():
            previous.cancel()

        task = self.runtime.start_background_task(
            manage_agents(self.runtime, self.mind),
            name="tui agents menu",
        )
        self._agent_menu_task = task
        task.add_done_callback(self._forget_agent_menu)

    def _forget_agent_menu(self, task: asyncio.Task[None]) -> None:
        """回收已完成的子执行线程菜单任务。"""
        if self._agent_menu_task is task:
            self._agent_menu_task = None

    async def _choose_effort(self) -> None:
        """选择并持久化模型推理强度。"""
        await self.state.refresh_preferences(self.mind, ttl_sec=0.0)
        primary = self.state.pref_config.get("primary")
        current = primary if isinstance(primary, dict) else {}

        selected = await choose_model_effort(
            self.runtime,
            current.get("reasoning_effort"),
        )

        if selected is None:
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
        self._present(command_result_block(
            "/model",
            TextSpan(model or "(empty)", BRIGHT_STYLE),
        ))
        self._present()

    async def _dispatch_mcp(self, mcp_action: McpAction | None) -> None:
        """执行即时 MCP 操作或建立可取消前台任务。"""
        action = mcp_action
        if action is None:
            action = await choose_mcp_action(self.runtime, self.mind)

        if action in {"start", "force", "restart", "stop"}:
            self.foreground_tasks.start_external_mcp(action)
            await self.foreground_tasks.wait()
            return None

        if action == "status":
            render_mcp_status(self.mind)
        else:
            self._present()

    async def _resume_conversation(self) -> None:
        """选择并恢复最近的会话。"""
        records = self.mind.recent_conversation_sessions(
            workspace=self.mind.history_workspace,
            sources=INTERACTIVE_HISTORY_SOURCES,
        )
        if not records:
            self._present(command_result_block(
                "/resume",
                TextSpan(
                    "No resumable conversations in the last 24 hours.",
                    MUTED_STYLE,
                ),
            ))
            self._present()
            return None

        selected = await choose_history_session(self.runtime, records)
        if selected is None:
            self._present()
            return None

        session_id = str(selected.get("sid") or "").strip()

        replay_blocks = await asyncio.to_thread(
            load_history_transcript,
            self.mind,
            session_id,
            terminal_width=self.runtime.terminal_width,
        )

        resumed = await self.mind.resume_conversation(
            selected,
            source="tui:resume",
        )
        if resumed is None:
            self._present(command_result_block(
                "/resume",
                TextSpan("Failed: invalid session cursor.", FAILURE_STYLE),
            ))
            self._present()
            return None

        self._clear_prompt_draft()
        self.runtime.replace_transcript(replay_blocks)

        self._present(command_result_block(
            "/resume",
            TextSpan("Resumed", BRIGHT_STYLE),
            TextSpan(
                f" · cid={resumed['cid']} sid={resumed['sid']}",
                MUTED_STYLE,
            ),
        ))
        self._present()

    def _finish_conversation_fork(self, status: ForkLiveStatus) -> None:
        """在普通会话分支成功后清理旧草稿并展示结果。"""
        if status.succeeded:
            self._clear_prompt_draft()
        render_fork_result(self.mind, status)

    def _clear_prompt_draft(self) -> None:
        """清除当前会话尚未提交的结构化草稿。"""
        self.state.clear_pending_prompt_extras()
        self.mind.attach.clear_pending_attachments()

    def _present(
        self,
        renderable: FragmentBlock | StyledBlock | None = None,
        *,
        view_type: str = "tui.output",
    ) -> None:
        """发送命令分派产生的正文展示。"""
        resolved_type = view_type

        if renderable is None and view_type == "tui.output":
            resolved_type = "tui.gap"
        self.application.emit(ApplicationView(
            type=resolved_type,
            renderable=renderable,
        ))


if __name__ == '__main__':
    pass
