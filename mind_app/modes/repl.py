# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import typing
from engine.tinker import (
    FileAssist, MindError
)
from mind_nova.modes import (
    DEFAULT_RUN_MODE, RunMode
)
from mind_nova.requests import (
    DEFAULT_ACCESS_MODE,
    access_mode_label,
    normalize_access_mode
)
from mind_app.interaction import PromptContext
from mind_app.frontend import ApplicationView
from .support.repl_commands import (
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
from .support.repl_diff import print_current_apply_patch_diff
from .support.repl_prompt import (
    WORKSPACE_LABEL_REFRESH,
    exec_status_display_label,
    fetch_runtime_workspace_root,
    ignored_repl_input,
    primary_model_prompt_label,
    primary_model_from_config,
    workspace_display_label
)
from .support.repl_mcp import (
    choose_mcp_action,
    run_mcp_action
)
from .support.repl_model import (
    choose_model_effort,
    render_model_effort_status
)
from .support.repl_permissions import (
    choose_permissions_mode,
    render_permissions_status
)
from .support.repl_ps import (
    choose_exec_session,
    watch_exec_session
)
from .support.repl_shell import run_shell_escape
from .support.repl_turn import run_repl_model_turn
from server import config_service_base_url
from ..history.resume_menu import choose_history_session

if typing.TYPE_CHECKING:
    from ..mind_core import Mind

MODE_BY_COMMAND: dict[str, RunMode] = {
    "/chat": "chat",
    "/fast": "fast",
    "/xtra": "xtra"
}


async def mind_loop(mind: "Mind") -> None:
    """交互式循环入口：处理命令切换、参数更新和模式调度。"""
    application = mind.frontend.application

    def present(
        renderable: typing.Any = None,
        *,
        view_type: str = "repl.output",
    ) -> None:
        application.emit(ApplicationView(
            type=view_type,
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

    doc = """\
        [bold]
        [bold #FFD75F]/chat[/]                     对话模式（交互能力协作/自然语言交互）
        [bold #FFD75F]/fast[/]                     高速模式（高吞吐任务流/数据媒体直达）
        [bold #FFD75F]/xtra[/]                     外接模式（外部 MCP 工具 + 通用工具 + 编码工具）
        [bold #AFD7FF]/new[/]                      开始新对话（保留模式、模型和待发送附件）
        [bold #5FD7AF]/resume[/]                   从当前模式最近 24 小时会话中恢复
        [bold #AFD7FF]/attach <path|dir|glob>[/]   添加本轮待发送附件（任意文件）
        [bold #AFD7FF]/attachments[/]              查看当前待发送附件
        [bold #AFD7FF]/detach <index|path>[/]      移除一个待发送附件
        [bold #AFD7FF]/attach-clear[/]             清空当前待发送附件
        [bold #5FD7AF]/permissions[/]              切换权限模式
        [bold #7F8C9A]/model <model-id>[/]         持久化主模型 ID；省略 model-id 表示清空
        [bold #5FD7AF]/effort[/]                   设置主模型推理强度
        [bold #AFD7FF]/preferences[/]              打开偏好配置页面
        [bold #AFD7FF]/compact[/]                  压缩当前对话上下文
        [bold #AFD7FF]/tools[/]                    查看当前可用 MCP 工具
        [bold #AFD7FF]/diff[/]                     查看本轮补丁净差异
        [bold #AFD7FF]/copy[/]                     复制最近一次助手回复原文
        [bold #5FD7AF]/ps[/]                       查看运行中的命令
        [bold #87D7FF]/mcp[/]                      管理外部 MCP 服务
        [bold #5FD7AF]/helix-link[/]               接入 Helix MCP
        [bold #5FD7AF]/helix-unlink[/]             移除当前会话的 Helix MCP
        [bold #5FD7AF]/helix-home[/]               打开 Helix 首页
        [bold #5FD7AF]/helix-stop[/]               停止 Helix 服务
        [bold #AFD7FF]/help, /h[/]                 指令索引（用法/示例/约定）
        [bold #5FD7AF]/license, /lic[/]            授权许可（License/特性）
        [bold #FF5F5F]/shutdown[/]                 关闭前台并停止本地运行时
        [bold #FF5F5F]/quit, /q, quit, exit[/]     断开会话（安全退出）
        [/]"""

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
            refreshed_at = now

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

        if ignored_repl_input(prompt_text):
            present()
            continue

        if await run_shell_escape(application, prompt_text):
            present()
            continue

        command = prompt_text.strip().lower()

        if command in quit_set:
            mind.task_event.set()
            break

        if command in help_set:
            present(doc, view_type="repl.help")
            continue

        if command in seal_set:
            present(view_type="startup_logo")
            continue

        if command in new_set:
            new_conversation_metadata = mind.reset_conversation(
                reason="command:/new",
                source="repl:new"
            )
            present(
                f"[bold #AFC7D8]New conversation[/] "
                f"[dim #7F8C9A]· cid={new_conversation_metadata['cid']} "
                f"sid={new_conversation_metadata['sid']}[/]"
            )
            present()
            continue

        if command in attachments_set:
            print_pending_attachments(mind)
            continue

        if command in attach_clear_set:
            count = mind.attach.clear_pending_attachments()
            present(f"[bold #AFC7D8]Cleared {count} pending attachment(s).[/]")
            present()
            continue

        if command in shutdown_set:
            mind.stop_runtime_on_exit = True
            mind.task_event.set()
            present("[bold #AFC7D8]Shutdown[/] [dim #7F8C9A]· stop backend runtime[/]")
            present()
            break

        if command.split(maxsplit=1)[0] in permissions_set:
            selected_access_mode = await choose_permissions_mode(access_mode)
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
                primary.get("reasoning_effort")
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
            session_id = await choose_exec_session(mind)
            if await watch_exec_session(mind, session_id):
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
                    present(
                        f"[bold #AFC7D8]Model saved[/] "
                        f"[bold #F4F7FA]{model_label}[/]"
                    )
                    present()
            continue

        if command in preferences_set:
            url = f"{config_service_base_url()}/pref"
            present(f"[bold #AFC7D8]Preferences[/] [dim #7F8C9A]· {url}[/]")
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
            mcp_action = await choose_mcp_action(mind)
            await run_mcp_action(mind, mcp_action)
            continue

        if command in resume_set:
            records = mind.recent_conversation_sessions()
            if not records:
                present(
                    "[bold #7F8C9A]No resumable conversations in the last 24 hours.[/]"
                )
                present()
                continue

            selected_record = await choose_history_session(records)
            if selected_record is None:
                present()
                continue

            resumed = mind.resume_conversation(selected_record, source="repl:resume")
            if resumed is None:
                present("[bold #FF5F5F]Resume failed: invalid session cursor.[/]")
                present()
                continue

            present(
                f"[bold #AFC7D8]Resumed[/] "
                f"[dim #7F8C9A]· cid={resumed['cid']} sid={resumed['sid']}[/]"
            )
            present()
            continue

        if command in MODE_BY_COMMAND:
            present()
            mode = MODE_BY_COMMAND[command]
            continue

        if m := re_attach.match(prompt_text):
            value = m.group(1).strip() if m.group(1) else ""
            if not value:
                present("[bold #FF5F5F]attach invalid: /attach <path|dir|glob>[/]")
                present()
                continue
            try:
                result = mind.attach.add_pending_attachments(value)
            except MindError as attach_error:
                present(f"[bold #FF5F5F]{attach_error}[/]")
                present()
                continue

            added    = result.get("added") or []
            existing = result.get("existing") or []
            skipped  = result.get("skipped") or []

            present(
                f"[bold #5FD7AF]Attach summary[/] "
                f"[bold #F4F7FA]{len(added)} added[/] "
                f"[#7F8C9A]· {len(existing)} existing · {len(skipped)} skipped[/]"
            )
            for added_attachment in added[:5]:
                present(
                    f"[bold #AFC7D8]  +[/] "
                    f"[bold #F4F7FA]{added_attachment.get('filename') or '-'}[/] "
                    f"[#7F8C9A]({added_attachment.get('kind') or 'file'})[/]"
                )
            if len(added) > 5:
                present(f"[#7F8C9A]  ... and {len(added) - 5} more added[/]")
            if skipped:
                present(
                    f"[#FFB86B]Skipped[/] "
                    f"{', '.join(str(skipped_attachment.get('filename') or '-') for skipped_attachment in skipped[:3])}"
                )
            present()
            continue

        if m := re_detach.match(prompt_text):
            value = m.group(1).strip() if m.group(1) else ""
            if not value:
                present("[bold #FF5F5F]detach invalid: /detach <index|path>[/]")
                present()
                continue
            try:
                removed_attachment = mind.attach.remove_pending_attachment(value)
            except MindError as detach_error:
                present(f"[bold #FF5F5F]{detach_error}[/]")
                present()
                continue

            present(
                f"[bold #AFC7D8]Detached[/] "
                f"[bold #F4F7FA]{removed_attachment.get('filename') or '-'}[/]"
            )
            present()
            continue

        pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
        present()
        mind.native_coding.reset_patch_diff()

        await run_repl_model_turn(
            mind,
            message_text=prompt_text,
            run_mode=mode,
            pref_config=pref_config,
            access_mode=access_mode
        )

    return None


if __name__ == '__main__':
    pass
