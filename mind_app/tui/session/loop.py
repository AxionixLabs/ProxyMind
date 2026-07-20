# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import typing
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
from mind_app.frontend import ApplicationView
from mind_app.presentation.models import (
    TextSpan,
    TextStyle
)
from mind_app.runtime.environment.workspace import fetch_runtime_workspace_root
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
    run_mcp_action
)
from ..features.model import (
    choose_model_effort,
    render_model_effort_status
)
from ..features.permissions import (
    choose_permissions_mode,
    render_permissions_status
)
from ..features.processes import (
    choose_exec_session,
    watch_exec_session
)
from ..features.shell import (
    parse_shell_escape,
    run_shell_escape
)
from .turn import run_tui_model_turn
from ..core.runtime import TuiRuntime
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

if typing.TYPE_CHECKING:
    from ...controller import Mind

MODE_BY_COMMAND: dict[str, RunMode] = {
    "/chat": "chat",
    "/fast": "fast",
    "/xtra": "xtra"
}

HELP_ITEMS: tuple[tuple[str, str, TextStyle], ...] = (
    ("/chat", "对话模式（交互能力协作/自然语言交互）", WARNING_STYLE),
    ("/fast", "高速模式（高吞吐任务流/数据媒体直达）", WARNING_STYLE),
    ("/xtra", "外接模式（外部 MCP 工具 + 通用工具 + 编码工具）", WARNING_STYLE),
    ("/new", "开始新对话（保留模式、模型和待发送附件）", ACCENT_STYLE),
    ("/resume", "从当前模式最近 24 小时会话中恢复", SUCCESS_STYLE),
    ("/attach <path|dir|glob>", "添加本轮待发送附件（任意文件）", ACCENT_STYLE),
    ("/attachments", "查看当前待发送附件", ACCENT_STYLE),
    ("/detach <index|path>", "移除一个待发送附件", ACCENT_STYLE),
    ("/attach-clear", "清空当前待发送附件", ACCENT_STYLE),
    ("/permissions", "切换权限模式", SUCCESS_STYLE),
    ("/model <model-id>", "持久化主模型 ID；省略 model-id 表示清空", MUTED_STYLE),
    ("/effort", "设置主模型推理强度", SUCCESS_STYLE),
    ("/preferences", "打开偏好配置页面", ACCENT_STYLE),
    ("/compact", "压缩当前对话上下文", ACCENT_STYLE),
    ("/tools", "查看当前可用 MCP 工具", ACCENT_STYLE),
    ("/diff", "查看本轮补丁净差异", ACCENT_STYLE),
    ("/copy", "复制最近一次助手回复原文", ACCENT_STYLE),
    ("/ps", "查看运行中的命令", SUCCESS_STYLE),
    ("/mcp", "管理外部 MCP 服务", TextStyle(foreground="#87D7FF", bold=True)),
    ("/helix-link", "接入 Helix MCP", SUCCESS_STYLE),
    ("/helix-unlink", "移除当前会话的 Helix MCP", SUCCESS_STYLE),
    ("/helix-home", "打开 Helix 首页", SUCCESS_STYLE),
    ("/helix-stop", "停止 Helix 服务", SUCCESS_STYLE),
    ("/help, /h", "指令索引（用法/示例/约定）", ACCENT_STYLE),
    ("/license, /lic", "授权许可（License/特性）", SUCCESS_STYLE),
    ("/shutdown", "关闭前台并停止本地运行时", FAILURE_STYLE),
    ("/quit, /q, quit, exit", "断开会话（安全退出）", FAILURE_STYLE),
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


async def run_tui_loop(mind: "Mind") -> None:
    """运行 TUI 交互状态机并调度命令和模型轮次。"""
    application = mind.frontend.application
    runtime     = typing.cast(TuiRuntime, mind.frontend.runtime)

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

    quit_set: set[str] = {"/quit", "/q", "quit", "exit"}
    help_set: set[str] = {"/help", "/h"}
    seal_set: set[str] = {"/license", "/lic"}
    new_set: set[str]  = {"/new"}

    attachments_set: set[str]  = {"/attachments"}
    attach_clear_set: set[str] = {"/attach-clear"}
    resume_set: set[str]       = {"/resume"}
    permissions_set: set[str]  = {"/permissions"}
    tools_set: set[str]        = {"/tools"}
    diff_set: set[str]         = {"/diff"}
    copy_set: set[str]         = {"/copy"}
    effort_set: set[str]       = {"/effort"}
    ps_set: set[str]           = {"/ps"}
    preferences_set: set[str]  = {"/preferences"}
    compact_set: set[str]      = {"/compact"}
    helix_link_set: set[str]   = {"/helix-link"}
    helix_unlink_set: set[str] = {"/helix-unlink"}
    helix_home_set: set[str]   = {"/helix-home"}
    helix_stop_set: set[str]   = {"/helix-stop"}
    shutdown_set: set[str]     = {"/shutdown"}

    doc = _help_block()

    re_attach = re.compile(r"^\s*/attach(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_detach = re.compile(r"^\s*/detach(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_model  = re.compile(r"^\s*/model(?:\s+(.+))?\s*$", re.IGNORECASE)

    pref_config = await mind.fresh_pref_config()

    primary = pref_config.get("primary") or {}
    model   = primary.get("model", "")

    mode: RunMode = DEFAULT_RUN_MODE
    access_mode   = DEFAULT_ACCESS_MODE

    workspace_label = ""
    refreshed_at    = 0.0

    while not mind.task_event.is_set():
        pref_config = await mind.fresh_pref_config()
        model       = primary_model_from_config(pref_config, model)
        now         = time.monotonic()

        if (
            refreshed_at <= 0.0
            or now - refreshed_at >= WORKSPACE_LABEL_REFRESH
        ):
            runtime_workspace_root = await fetch_runtime_workspace_root()
            if runtime_workspace_root is not None:
                mind.set_history_workspace(runtime_workspace_root)

            workspace_label = workspace_display_label(runtime_workspace_root)
            refreshed_at    = now

        try:
            exec_status_label = exec_status_display_label(
                await mind.native_coding.running_exec_sessions(),
                line_width=application.viewport.width
            )
            prompt_text = await mind.frontend.interaction.read_message(PromptContext(
                mode=mode,
                model=primary_model_prompt_label(pref_config, model),
                workspace_label=workspace_label,
                access_label=access_mode_label(access_mode),
                exec_status_label=exec_status_label
            ))
        except KeyboardInterrupt:
            mind.exit_code = 130
            mind.task_event.set()
            break
        except (EOFError, UnicodeDecodeError):
            continue

        if ignored_tui_input(prompt_text):
            continue

        if prompt_text.startswith("!"):
            present()
            shell_request = parse_shell_escape(prompt_text)
            if shell_request is not None and shell_request.enter_shell:
                shell_handled = await run_modal(
                    lambda: run_shell_escape(application, prompt_text)
                )
            else:
                shell_handled = await run_shell_escape(application, prompt_text)
            if shell_handled:
                present()
                continue

        command = prompt_text.strip().lower()
        if command.startswith("/"):
            present()

        if command in quit_set:
            mind.task_event.set()
            break

        if command in help_set:
            present(doc, view_type="tui.help")
            continue

        if command in seal_set:
            present(view_type="startup_logo")
            continue

        if command in new_set:
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

        if command in attachments_set:
            print_pending_attachments(mind)
            continue

        if command in attach_clear_set:
            count = mind.attach.clear_pending_attachments()
            present(text_block(
                f"Cleared {count} pending attachment(s).",
                ACCENT_STYLE,
            ))
            present()
            continue

        if command in shutdown_set:
            mind.stop_runtime_on_exit = True
            mind.task_event.set()
            present(_label_detail("Shutdown", "stop backend runtime"))
            present()
            break

        if command.split(maxsplit=1)[0] in permissions_set:
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

        if command in tools_set:
            pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
            await print_available_tools(mind, run_mode=mode, pref_config=pref_config)
            continue

        if command in diff_set:
            print_current_apply_patch_diff(mind)
            continue

        if command in copy_set:
            await copy_last_assistant_reply(mind)
            continue

        if command in effort_set:
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
                render_model_effort_status(
                    application,
                    saved_primary.get("reasoning_effort") or selected_effort
                )
            continue

        if command in ps_set:
            session_id = await choose_exec_session(
                runtime,
                mind,
            )
            if await watch_exec_session(
                runtime,
                mind,
                session_id,
            ):
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

        if command in preferences_set:
            url = f"{config_service_base_url()}/pref"
            present(_label_detail("Preferences", url))
            await FileAssist.open_url(url)
            present()
            continue

        if command in compact_set:
            pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
            await compact_current_conversation(
                mind,
                run_mode=mode,
                pref_config=pref_config
            )
            continue

        if command in helix_link_set:
            await link_helix_runtime(mind)
            refreshed_at = 0.0
            continue

        if command in helix_unlink_set:
            unlink_helix_runtime(mind)
            refreshed_at = 0.0
            continue

        if command in helix_home_set:
            await open_helix_home(mind)
            refreshed_at = 0.0
            continue

        if command in helix_stop_set:
            await stop_helix_runtime(mind)
            refreshed_at = 0.0
            continue

        if command == "/mcp":
            mcp_action = await choose_mcp_action(
                runtime,
                mind,
            )
            await run_mcp_action(mind, mcp_action)
            continue

        if command in resume_set:
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
            present()
            mode = MODE_BY_COMMAND[command]
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

        runtime.set_execution_active(True)
        try:
            await run_tui_model_turn(
                mind,
                message_text=prompt_text,
                run_mode=mode,
                pref_config=pref_config,
                access_mode=access_mode
            )
        finally:
            runtime.set_execution_active(False)

    return None


if __name__ == '__main__':
    pass
