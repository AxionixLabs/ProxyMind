# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import typing
import asyncio
from prompt_toolkit.utils import get_cwidth
from engine.errors import MindError
from engine.file_assist import FileAssist
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    RunMode
)
from mind_nova.requests.access import (
    DEFAULT_ACCESS_MODE,
    access_mode_label,
    normalize_access_mode
)
from mind_app.interaction import PromptContext
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from mind_app.presentation.models import (
    TextSpan,
    TextStyle
)
from mind_app.runtime.environment.workspace import fetch_runtime_workspace_root
from mind_app.runtime.mcp.service_runtime import service_runtime_asset_missing
from ..features.commands import (
    exchange_pref_value,
    compact_current_conversation,
    persist_primary_pref,
    print_available_tools,
    print_pending_attachments,
    copy_last_assistant_reply,
    link_helix_runtime,
    open_helix_home,
    stop_helix_runtime,
    unlink_helix_runtime
)
from ..features.diff import print_current_apply_patch_diff
from ..features.context import (
    WORKSPACE_LABEL_REFRESH,
    exec_status_display_label,
    ignored_tui_input,
    primary_model_prompt_label,
    primary_model_from_config,
    workspace_display_label
)
from ..features.mcp import (
    choose_mcp_action,
    parse_mcp_command,
    run_mcp_action,
    start_mcp_runtime,
)
from ..features.model import (
    choose_model_effort,
    render_model_effort_status
)
from ..features.mode import render_mode_status
from ..features.permissions import (
    choose_permissions_mode,
    render_permissions_status
)
from ..features.processes import (
    manage_exec_sessions,
    monitor_exec_status,
)
from ..features.shell import (
    parse_shell_escape,
    run_shell_escape
)
from .turn import run_tui_model_turn
from ..core.runtime import (
    TuiRuntime,
    require_tui_runtime,
)
from ..core.models import FragmentBlock
from ..core.styles import (
    ACCENT_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    SUCCESS_STYLE,
    WARNING_STYLE,
    fragment_block,
    text_block,
)
from server import config_service_base_url
from ..features.history import choose_history_session
from ..prompting.commands import (
    TUI_COMMANDS,
    command_spec,
    matches_command,
)

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

_HELP_STYLE_BY_KEY: typing.Final[dict[str, TextStyle]] = {
    "chat": WARNING_STYLE,
    "fast": WARNING_STYLE,
    "xtra": WARNING_STYLE,
    "resume": SUCCESS_STYLE,
    "permissions": SUCCESS_STYLE,
    "model": MUTED_STYLE,
    "effort": SUCCESS_STYLE,
    "ps": SUCCESS_STYLE,
    "mcp": TextStyle(foreground="#87D7FF", bold=True),
    "helix_link": SUCCESS_STYLE,
    "helix_unlink": SUCCESS_STYLE,
    "helix_home": SUCCESS_STYLE,
    "helix_stop": SUCCESS_STYLE,
    "license": SUCCESS_STYLE,
    "shutdown": FAILURE_STYLE,
    "quit": FAILURE_STYLE,
}
HELP_ITEMS: tuple[tuple[str, str, TextStyle], ...] = tuple(
    (
        command.usage,
        command.help_detail,
        _HELP_STYLE_BY_KEY.get(command.key, ACCENT_STYLE),
    )
    for command in TUI_COMMANDS
    if command.show_in_help
)


def _help_block() -> FragmentBlock:
    """生成 TUI 命令索引块。"""
    command_width = max(get_cwidth(command) for command, _detail, _style in HELP_ITEMS) + 3
    parts: list[TextSpan] = []
    for index, (command, detail, style) in enumerate(HELP_ITEMS):
        if index:
            parts.append(TextSpan("\n"))
        padding = " " * max(1, command_width - get_cwidth(command))
        parts.extend([
            TextSpan(command, style),
            TextSpan(padding),
            TextSpan(detail),
        ])
    return fragment_block(*parts)


def _label_detail(label: str, detail: str) -> FragmentBlock:
    """生成标题和次要详情组成的会话状态块。"""
    return fragment_block(
        TextSpan(f"{label} ", ACCENT_STYLE),
        TextSpan(f"· {detail}", MUTED_STYLE),
    )


def _failure_block(message: typing.Any) -> FragmentBlock:
    """生成单行会话错误块。"""
    return text_block(str(message), FAILURE_STYLE)


def _interruption_block() -> FragmentBlock:
    """生成当前响应被用户中断后的引导块。"""
    return fragment_block(
        TextSpan("■", FAILURE_STYLE),
        TextSpan(" Response interrupted", BRIGHT_STYLE),
        TextSpan(" · Tell Mind what to do differently.", MUTED_STYLE),
    )


async def _execute_tui_model_turn(
    application: ApplicationSink,
    runtime: TuiRuntime,
    turn: typing.Coroutine[typing.Any, typing.Any, None],
    *,
    stream_command_handler: typing.Callable[
        [str, typing.Callable[[], bool]],
        bool,
    ] | None = None,
    show_interrupt_notice: typing.Callable[[], bool] = lambda: True,
) -> None:
    """执行可由主输入区定向取消的单个模型轮次。"""
    task = asyncio.create_task(turn, name="mind tui model turn")
    interrupted = False
    runtime.set_execution_active(True)
    runtime.bind_turn_interrupt(task.cancel)
    if stream_command_handler is not None:
        runtime.bind_stream_command_handler(
            lambda value: stream_command_handler(value, task.cancel)
        )
    try:
        await task
    except asyncio.CancelledError:
        if not runtime.consume_turn_interrupt():
            raise
        interrupted = True
    else:
        interrupted = runtime.consume_turn_interrupt()
    finally:
        if not interrupted:
            runtime.consume_turn_interrupt()
        runtime.bind_stream_command_handler(None)
        runtime.bind_turn_interrupt(None)
        runtime.set_execution_active(False)

    if interrupted and show_interrupt_notice():
        application.emit(ApplicationView(
            type="tui.interrupted",
            renderable=_interruption_block(),
        ))


async def preload_tui_prompt_context(mind: "Mind") -> None:
    """在 TUI 首帧前加载输入上下文和后台进程状态。"""
    runtime = require_tui_runtime(mind.frontend.runtime)

    pref_result, workspace_result, exec_result = await asyncio.gather(
        mind.fresh_pref_config(ttl_sec=0.0),
        fetch_runtime_workspace_root(),
        mind.native_coding.running_exec_sessions(),
        return_exceptions=True,
    )

    pref_config = pref_result if isinstance(pref_result, dict) else {}

    runtime_workspace_root = (
        workspace_result
        if not isinstance(workspace_result, BaseException)
        else None
    )

    exec_snapshot = exec_result if isinstance(exec_result, dict) else {}

    if runtime_workspace_root is not None:
        mind.set_history_workspace(runtime_workspace_root)

    runtime.set_prompt_context(PromptContext(
        mode=DEFAULT_RUN_MODE,
        model=primary_model_prompt_label(pref_config),
        workspace_label=workspace_display_label(runtime_workspace_root),
        access_label=access_mode_label(DEFAULT_ACCESS_MODE),
    ))

    runtime.set_process_status_label(exec_status_display_label(
        exec_snapshot,
        line_width=runtime.terminal_width,
    ))


async def run_tui_loop(mind: "Mind") -> None:
    """运行 TUI 交互状态机并调度命令和模型轮次。"""
    application = mind.frontend.application
    runtime     = require_tui_runtime(mind.frontend.runtime)

    runtime.start_background_task(
        monitor_exec_status(runtime, mind),
        name="mind process status",
    )

    stream_barriers: dict[str, asyncio.Task[None]] = {}

    async def run_modal(
        factory: typing.Callable[[], typing.Awaitable[typing.Any]],
    ) -> typing.Any:
        """在前端运行期内执行一项独占终端交互。"""
        return await runtime.run_modal(factory)

    def present(
        renderable: FragmentBlock | None = None,
        *,
        view_type: str = "tui.output",
    ) -> None:
        resolved_type = view_type
        if renderable is None and view_type == "tui.output":
            resolved_type = "tui.gap"
        application.emit(ApplicationView(
            type=resolved_type,
            renderable=renderable,
        ))

    def defer_notice(message: str) -> None:
        """在当前流式正文结束后展示命令状态。"""
        asyncio.get_running_loop().call_soon(
            runtime.queue_background_block,
            text_block(message, MUTED_STYLE),
        )

    def start_stream_barrier(
        key: str,
        factory: typing.Callable[[], typing.Coroutine[typing.Any, typing.Any, None]],
    ) -> bool:
        """合并同类后台启动任务并注册下一轮屏障。"""
        active = stream_barriers.get(key)
        if active is not None and not active.done():
            defer_notice(f"{key} startup is already in progress.")
            return True

        task = runtime.start_background_task(
            factory(),
            name=f"mind tui stream {key}",
        )
        stream_barriers[key] = task

        def forget(completed: asyncio.Task[None]) -> None:
            """移除已经完成的同类启动屏障。"""
            if stream_barriers.get(key) is completed:
                stream_barriers.pop(key, None)

        task.add_done_callback(forget)
        return True

    def cancel_stream_barriers() -> None:
        """取消尚未完成的后台启动屏障。"""
        for task in tuple(stream_barriers.values()):
            if not task.done():
                task.cancel()

    def handle_stream_command(
        value: str,
        cancel_turn: typing.Callable[[], bool],
    ) -> bool:
        """分派允许在模型流式输出期间执行的命令。"""
        command = str(value or "").strip().casefold()
        if matches_command(command, "quit"):
            runtime.request_turn_interrupt()
            mind.task_event.set()
            cancel_stream_barriers()
            cancel_turn()
            return True
        if matches_command(command, "shutdown"):
            runtime.request_turn_interrupt()
            mind.stop_runtime_on_exit = True
            mind.task_event.set()
            cancel_stream_barriers()
            cancel_turn()
            return True
        if matches_command(command, "helix_link"):
            if mind.is_service_mcp_linked():
                defer_notice("Helix MCP is already linked.")
                return True
            try:
                context = mind.require_service_runtime_context()
            except MindError:
                return False
            if service_runtime_asset_missing(context):
                return False
            return start_stream_barrier(
                "Helix MCP",
                lambda: link_helix_runtime(mind),
            )

        is_mcp, mcp_action = parse_mcp_command(command)
        if not is_mcp or mcp_action not in {"start", "force"}:
            return False
        external_mcp = getattr(mind, "external_mcp", None)
        if bool(getattr(external_mcp, "started", False)):
            if mcp_action == "start":
                defer_notice("External MCP is already started.")
                return True
            return False
        return start_stream_barrier(
            "External MCP",
            lambda: start_mcp_runtime(
                mind,
                include_disabled=mcp_action == "force",
            ),
        )

    def handle_barrier_command(value: str) -> bool:
        """在后台屏障等待期间只处理退出类命令。"""
        command = str(value or "").strip().casefold()
        if matches_command(command, "quit"):
            mind.task_event.set()
        elif matches_command(command, "shutdown"):
            mind.stop_runtime_on_exit = True
            mind.task_event.set()
        else:
            return False
        cancel_stream_barriers()
        return True

    async def wait_stream_barriers() -> None:
        """等待后台启动完成后再允许下一次模型调用。"""
        pending = tuple(
            task for task in stream_barriers.values()
            if not task.done()
        )
        if not pending:
            return None
        runtime.set_foreground_active(True)
        runtime.bind_stream_command_handler(handle_barrier_command)
        try:
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            runtime.bind_stream_command_handler(None)
            runtime.set_foreground_active(False)

    doc = _help_block()

    re_attach = re.compile(
        rf"^\s*{re.escape(command_spec('attach').command)}(?:\s+(.*))?\s*$",
        re.IGNORECASE,
    )
    re_detach = re.compile(
        rf"^\s*{re.escape(command_spec('detach').command)}(?:\s+(.*))?\s*$",
        re.IGNORECASE,
    )
    re_model = re.compile(
        rf"^\s*{re.escape(command_spec('model').command)}(?:\s+(.+))?\s*$",
        re.IGNORECASE,
    )

    pref_config = mind.pref.to_config()

    primary = pref_config.get("primary") or {}
    model   = primary.get("model", "")

    mode: RunMode = DEFAULT_RUN_MODE
    access_mode   = DEFAULT_ACCESS_MODE

    workspace_label = runtime.context.workspace_label
    refreshed_at    = time.monotonic()

    while not mind.task_event.is_set():
        await wait_stream_barriers()
        if mind.task_event.is_set():
            break
        prompt_task = asyncio.create_task(
            mind.frontend.interaction.read_message(PromptContext(
                mode=mode,
                model=primary_model_prompt_label(pref_config, model),
                workspace_label=workspace_label,
                access_label=access_mode_label(access_mode),
            )),
            name="mind tui read message",
        )
        try:
            pref_config = await mind.fresh_pref_config()
            model       = primary_model_from_config(pref_config, model)
            now         = time.monotonic()

            if now - refreshed_at >= WORKSPACE_LABEL_REFRESH:
                runtime_workspace_root = await fetch_runtime_workspace_root()
                if runtime_workspace_root is not None:
                    mind.set_history_workspace(runtime_workspace_root)

                workspace_label = workspace_display_label(runtime_workspace_root)
                refreshed_at    = now

            runtime.set_prompt_context(PromptContext(
                mode=mode,
                model=primary_model_prompt_label(pref_config, model),
                workspace_label=workspace_label,
                access_label=access_mode_label(access_mode),
            ))
            prompt_text = await prompt_task
        except KeyboardInterrupt:
            mind.exit_code = 130
            mind.task_event.set()
            break
        except EOFError:
            mind.task_event.set()
            break
        except UnicodeDecodeError:
            continue
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
            await asyncio.gather(prompt_task, return_exceptions=True)

        if ignored_tui_input(prompt_text):
            continue

        if prompt_text.startswith("!"):
            present()
            shell_request = parse_shell_escape(prompt_text)
            if shell_request is not None and shell_request.enter_shell:
                shell_handled = await run_modal(
                    lambda: run_shell_escape(runtime, mind, prompt_text)
                )
            else:
                shell_handled = await run_shell_escape(runtime, mind, prompt_text)
            if shell_handled:
                present()
                continue

        command = prompt_text.strip().lower()
        if command.startswith("/"):
            present()

        if matches_command(command, "quit"):
            mind.task_event.set()
            break

        if matches_command(command, "help"):
            present(doc, view_type="tui.help")
            continue

        if matches_command(command, "license"):
            present(view_type="startup_logo")
            continue

        if matches_command(command, "new"):
            new_conversation_metadata = mind.reset_conversation(
                reason="command:/new",
                source="tui:new"
            )
            present(_label_detail(
                "New conversation",
                f"cid={new_conversation_metadata['cid']} "
                f"sid={new_conversation_metadata['sid']}",
            ))
            present()
            continue

        if matches_command(command, "attachments"):
            print_pending_attachments(mind)
            continue

        if matches_command(command, "attach_clear"):
            count = mind.attach.clear_pending_attachments()
            present(text_block(
                f"Cleared {count} pending attachment(s).",
                ACCENT_STYLE,
            ))
            present()
            continue

        if matches_command(command, "shutdown"):
            mind.stop_runtime_on_exit = True
            mind.task_event.set()
            present(_label_detail("Shutdown", "stop backend runtime"))
            present()
            break

        if matches_command(command.split(maxsplit=1)[0], "permissions"):
            selected_access_mode = await choose_permissions_mode(
                runtime,
                access_mode,
            )
            if selected_access_mode is not None:
                access_mode = normalize_access_mode(selected_access_mode)
                render_permissions_status(application, access_mode)
            else:
                present()
            continue

        if matches_command(command, "tools"):
            pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
            await print_available_tools(mind, run_mode=mode, pref_config=pref_config)
            continue

        if matches_command(command, "diff"):
            print_current_apply_patch_diff(mind)
            continue

        if matches_command(command, "copy"):
            await copy_last_assistant_reply(mind)
            continue

        if matches_command(command, "effort"):
            pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
            primary     = pref_config.get("primary") or {}

            selected_effort = await choose_model_effort(
                runtime,
                primary.get("reasoning_effort"),
            )

            if selected_effort is None:
                present()
                continue

            saved_primary = await persist_primary_pref(
                mind,
                command_name="model-effort",
                field_name="reasoning_effort",
                field_value=selected_effort
            )
            if saved_primary is not None:
                updated_primary = dict(pref_config.get("primary") or {})
                updated_primary.update(saved_primary)
                updated_primary["reasoning_effort"] = (
                    saved_primary.get("reasoning_effort") or selected_effort
                )
                pref_config = dict(pref_config)
                pref_config["primary"] = updated_primary
                model = primary_model_from_config(pref_config, model)
                runtime.set_prompt_context(PromptContext(
                    mode=mode,
                    model=primary_model_prompt_label(pref_config, model),
                    workspace_label=workspace_label,
                    access_label=access_mode_label(access_mode),
                ))
                render_model_effort_status(
                    application,
                    updated_primary["reasoning_effort"],
                )
            continue

        if matches_command(command, "ps"):
            if await manage_exec_sessions(runtime, mind):
                present()
            continue

        if m := re_model.match(prompt_text):
            model_value = await exchange_pref_value(
                application,
                m,
                pref_command="model",
            )
            if model_value is not None:
                saved_primary = await persist_primary_pref(
                    mind,
                    command_name="model",
                    field_name="model",
                    field_value=model_value
                )
                if saved_primary is not None:
                    model = str(saved_primary.get("model") or model_value)
                    model_label = model or "(empty)"
                    present(fragment_block(
                        TextSpan("Model saved ", ACCENT_STYLE),
                        TextSpan(model_label, BRIGHT_STYLE),
                    ))
                    present()
            continue

        if matches_command(command, "preferences"):
            url = f"{config_service_base_url()}/pref"
            present(_label_detail("Preferences", url))
            await FileAssist.open_url(url)
            present()
            continue

        if matches_command(command, "compact"):
            pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
            await compact_current_conversation(
                mind,
                run_mode=mode,
                pref_config=pref_config
            )
            continue

        if matches_command(command, "helix_link"):
            await link_helix_runtime(mind)
            refreshed_at = 0.0
            continue

        if matches_command(command, "helix_unlink"):
            unlink_helix_runtime(mind)
            refreshed_at = 0.0
            continue

        if matches_command(command, "helix_home"):
            await open_helix_home(mind)
            refreshed_at = 0.0
            continue

        if matches_command(command, "helix_stop"):
            await stop_helix_runtime(mind)
            refreshed_at = 0.0
            continue

        is_mcp_command, mcp_action = parse_mcp_command(command)
        if is_mcp_command:
            if mcp_action is None:
                mcp_action = await choose_mcp_action(
                    runtime,
                    mind,
                )
            await run_mcp_action(mind, mcp_action)
            continue

        if matches_command(command, "resume"):
            records = mind.recent_conversation_sessions()
            if not records:
                present(text_block(
                    "No resumable conversations in the last 24 hours.",
                    MUTED_STYLE,
                ))
                present()
                continue

            selected_record = await choose_history_session(
                runtime,
                records,
            )
            if selected_record is None:
                present()
                continue

            resumed = mind.resume_conversation(selected_record, source="tui:resume")
            if resumed is None:
                present(_failure_block("Resume failed: invalid session cursor."))
                present()
                continue

            present(_label_detail(
                "Resumed",
                f"cid={resumed['cid']} sid={resumed['sid']}",
            ))
            present()
            continue

        if command in MODE_BY_COMMAND:
            mode = MODE_BY_COMMAND[command]
            render_mode_status(application, mode)
            continue

        if m := re_attach.match(prompt_text):
            value = m.group(1).strip() if m.group(1) else ""
            if not value:
                present(_failure_block("attach invalid: /attach <path|dir|glob>"))
                present()
                continue
            try:
                result = mind.attach.add_pending_attachments(value)
            except MindError as attach_error:
                present(_failure_block(attach_error))
                present()
                continue

            added    = result.get("added") or []
            existing = result.get("existing") or []
            skipped  = result.get("skipped") or []

            present(fragment_block(
                TextSpan("Attach summary ", SUCCESS_STYLE),
                TextSpan(f"{len(added)} added ", BRIGHT_STYLE),
                TextSpan(
                    f"· {len(existing)} existing · {len(skipped)} skipped",
                    MUTED_STYLE,
                ),
            ))
            for added_attachment in added[:5]:
                present(fragment_block(
                    TextSpan("  + ", ACCENT_STYLE),
                    TextSpan(
                        f"{added_attachment.get('filename') or '-'} ",
                        BRIGHT_STYLE,
                    ),
                    TextSpan(
                        f"({added_attachment.get('kind') or 'file'})",
                        MUTED_STYLE,
                    ),
                ))
            if len(added) > 5:
                present(text_block(
                    f"  ... and {len(added) - 5} more added",
                    MUTED_STYLE,
                ))
            if skipped:
                present(fragment_block(
                    TextSpan("Skipped ", WARNING_STYLE),
                    TextSpan(
                        ", ".join(
                            str(item.get("filename") or "-")
                            for item in skipped[:3]
                        )
                    ),
                ))
            present()
            continue

        if m := re_detach.match(prompt_text):
            value = m.group(1).strip() if m.group(1) else ""
            if not value:
                present(_failure_block("detach invalid: /detach <index|path>"))
                present()
                continue
            try:
                removed_attachment = mind.attach.remove_pending_attachment(value)
            except MindError as detach_error:
                present(_failure_block(detach_error))
                present()
                continue

            present(fragment_block(
                TextSpan("Detached ", ACCENT_STYLE),
                TextSpan(
                    str(removed_attachment.get("filename") or "-"),
                    BRIGHT_STYLE,
                ),
            ))
            present()
            continue

        pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
        present()
        mind.native_coding.reset_patch_diff()

        await _execute_tui_model_turn(
            application,
            runtime,
            run_tui_model_turn(
                mind,
                message_text=prompt_text,
                run_mode=mode,
                pref_config=pref_config,
                access_mode=access_mode
            ),
            stream_command_handler=handle_stream_command,
            show_interrupt_notice=lambda: not mind.task_event.is_set(),
        )
        exit_reason = runtime.consume_exit_request()
        if exit_reason is not None:
            if exit_reason == "interrupt":
                mind.exit_code = 130
            mind.task_event.set()
            break

    return None


if __name__ == '__main__':
    pass
