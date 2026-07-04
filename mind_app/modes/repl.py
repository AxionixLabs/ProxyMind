# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import time
import typing
from engine.tinker import MindError
from mind_core.design import Design
from mind_nova.modes import (
    DEFAULT_RUN_MODE, RunMode
)
from mind_nova.requests import (
    DEFAULT_ACCESS_MODE,
    access_mode_label,
    normalize_access_mode
)
from .support.repl_commands import (
    print_attach_gap,
    print_available_tools,
    print_pending_attachments,
    start_helix_runtime
)
from .support.repl_prompt import (
    WORKSPACE_LABEL_REFRESH,
    fetch_runtime_workspace_root,
    ignored_repl_input,
    primary_model_from_config,
    workspace_display_label
)
from .support.repl_mcp import render_mcp_status
from .support.repl_permissions import (
    choose_permissions_mode,
    render_permissions_status
)
from .support.repl_shell import run_shell_escape
from .support.repl_turn import (
    print_turn_body_gap,
    run_repl_model_turn
)
from ..history.resume_menu import choose_history_session

if typing.TYPE_CHECKING:
    from ..mind_core import Mind

MODE_BY_COMMAND: dict[str, RunMode] = {
    "/chat": "chat",
    "/fast": "fast",
    "/plan": "plan",
    "/xtra": "xtra"
}


async def mind_loop(mind: "Mind") -> None:
    """交互式循环入口：处理命令切换、参数更新和模式调度。"""
    quit_set: set[str] = {"/quit", "/q", "quit", "exit"}
    help_set: set[str] = {"/help", "/h"}
    seal_set: set[str] = {"/license", "/lic"}
    new_set: set[str]  = {"/new"}

    attachments_set: set[str]  = {"/attachments"}
    attach_clear_set: set[str] = {"/attach-clear"}
    resume_set: set[str]       = {"/resume"}
    permissions_set: set[str]  = {"/permissions"}
    tools_set: set[str]        = {"/tools"}
    helix_start_set: set[str]  = {"/helix-start"}
    helix_stop_set: set[str]   = {"/helix-stop"}
    shutdown_set: set[str]     = {"/shutdown"}

    doc = """\
        [bold]
        [bold #FFD75F]/chat[/]                     对话模式（交互能力协作/自然语言交互）
        [bold #FFD75F]/fast[/]                     高速模式（高吞吐任务流/数据媒体直达）
        [bold #FFD75F]/plan[/]                     编排模式（结构任务拆解/确定路径执行）
        [bold #FFD75F]/xtra[/]                     外接模式（外部 MCP 工具 + 通用工具 + 编码工具）
        [bold #AFD7FF]/new[/]                      开始新对话（保留模式、模型和待发送附件）
        [bold #AFD7FF]/resume[/]                   从当前模式最近 24 小时会话中恢复
        [bold #AFD7FF]/attach <path|dir|glob>[/]   添加本轮待发送附件（任意文件）
        [bold #AFD7FF]/attachments[/]              查看当前待发送附件
        [bold #AFD7FF]/detach <index|path>[/]      移除一个待发送附件
        [bold #AFD7FF]/attach-clear[/]             清空当前待发送附件
        [bold #AFD7FF]/permissions[/]               切换权限模式
        [bold #AFD7FF]/tools[/]                    查看当前可用 MCP 工具
        [bold #AFD7FF]/mcp[/]                      查看外部 MCP runtime 状态
        [bold #AFD7FF]/helix-start[/]              启动本地 Helix 服务
        [bold #FF5F5F]/helix-stop[/]               停止本地 Helix 服务
        [bold #AFD7FF]/help, /h[/]                 指令索引（用法/示例/约定）
        [bold #5FD7AF]/license, /lic[/]            授权许可（License/特性）
        [bold #FF5F5F]/shutdown[/]                 关闭前台并停止本地运行时
        [bold #FF5F5F]/quit, /q, quit, exit[/]     断开会话（安全退出）
        [/]"""

    re_attach = re.compile(r"^\s*/attach(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_detach = re.compile(r"^\s*/detach(?:\s+(.*))?\s*$", re.IGNORECASE)

    pref_config = await mind.fresh_pref_config()

    primary = pref_config.get("primary") or {}
    model   = primary.get("model", "")

    mode: RunMode = DEFAULT_RUN_MODE
    access_mode   = DEFAULT_ACCESS_MODE

    workspace_label = ""
    workspace_label_refreshed_at = 0.0

    while not mind.task_event.is_set():
        pref_config = await mind.fresh_pref_config()
        model = primary_model_from_config(pref_config, model)

        now = time.monotonic()
        if (
            workspace_label_refreshed_at <= 0.0
            or now - workspace_label_refreshed_at >= WORKSPACE_LABEL_REFRESH
        ):
            runtime_workspace_root = await fetch_runtime_workspace_root()
            if runtime_workspace_root is not None:
                mind.set_history_workspace(runtime_workspace_root)

            workspace_label = workspace_display_label(runtime_workspace_root)
            workspace_label_refreshed_at = now

        try:
            prompt_text = await mind.prompt_box.prompt_async(
                mode=mode,
                model=model,
                workspace_label=workspace_label,
                access_label=access_mode_label(access_mode)
            )
        except KeyboardInterrupt:
            mind.exit_code = 130
            mind.task_event.set()
            break
        except (EOFError, UnicodeDecodeError):
            continue

        if ignored_repl_input(prompt_text):
            Design.console.print()
            continue

        if await run_shell_escape(prompt_text):
            Design.console.print()
            continue

        command = prompt_text.strip().lower()

        if command in quit_set:
            mind.task_event.set()
            break

        if command in help_set:
            Design.console.print(doc)
            continue

        if command in seal_set:
            Design.startup_logo()
            continue

        if command in new_set:
            new_conversation_metadata = mind.reset_conversation(
                reason="command:/new",
                mode=mode,
                source="repl:new"
            )
            Design.console.print(
                f"[bold #AFC7D8]New conversation[/] "
                f"[dim #7F8C9A]· cid={new_conversation_metadata['cid']} "
                f"sid={new_conversation_metadata['sid']}[/]"
            )
            Design.console.print()
            continue

        if command in attachments_set:
            print_pending_attachments(mind)
            continue

        if command in attach_clear_set:
            count = mind.attach.clear_pending_attachments()
            Design.console.print(f"[bold #AFC7D8]Cleared {count} pending attachment(s).[/]")
            print_attach_gap()
            continue

        if command in helix_stop_set:
            Design.console.print("[bold #AFC7D8]Helix[/] [dim #7F8C9A]· stop[/]")
            try:
                await mind.stop_service_runtime()
            except MindError as error:
                Design.console.print(f"[bold #FF5F5F]Helix stop failed: {error}[/]")
                Design.console.print()
                continue
            Design.console.print()
            continue

        if command in shutdown_set:
            mind.stop_runtime_on_exit = True
            mind.task_event.set()
            Design.console.print("[bold #AFC7D8]Shutdown[/] [dim #7F8C9A]· stop backend runtime[/]")
            Design.console.print()
            break

        if command.split(maxsplit=1)[0] in permissions_set:
            selected_access_mode = await choose_permissions_mode(access_mode)
            if selected_access_mode is not None:
                access_mode = normalize_access_mode(selected_access_mode)
                render_permissions_status(access_mode)
            else:
                Design.console.print()
            continue

        if command in tools_set:
            pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
            await print_available_tools(mind, run_mode=mode, pref_config=pref_config)
            continue

        if command in helix_start_set:
            await start_helix_runtime(mind)
            workspace_label_refreshed_at = 0.0
            continue

        if command == "/mcp":
            render_mcp_status(mind)
            continue

        if command in resume_set:
            records = mind.recent_conversation_sessions(mode=mode)
            if not records:
                Design.console.print(
                    f"[bold #7F8C9A]No resumable {mode} conversations in the last 24 hours.[/]"
                )
                Design.console.print()
                continue

            selected_record = await choose_history_session(records)
            if selected_record is None:
                Design.console.print()
                continue

            resumed = mind.resume_conversation(selected_record, mode=mode, source="repl:resume")
            if resumed is None:
                Design.console.print("[bold #FF5F5F]Resume failed: invalid session cursor.[/]")
                Design.console.print()
                continue

            Design.console.print(
                f"[bold #AFC7D8]Resumed[/] "
                f"[dim #7F8C9A]· cid={resumed['cid']} sid={resumed['sid']}[/]"
            )
            Design.console.print()
            continue

        if command in MODE_BY_COMMAND:
            Design.console.print()
            mode = MODE_BY_COMMAND[command]
            continue

        if m := re_attach.match(prompt_text):
            value = m.group(1).strip() if m.group(1) else ""
            if not value:
                Design.console.print("[bold #FF5F5F]attach invalid: /attach <path|dir|glob>[/]")
                print_attach_gap()
                continue
            try:
                result = mind.attach.add_pending_attachments(value)
            except MindError as attach_error:
                Design.console.print(f"[bold #FF5F5F]{attach_error}[/]")
                print_attach_gap()
                continue

            added    = result.get("added") or []
            existing = result.get("existing") or []
            skipped  = result.get("skipped") or []

            Design.console.print(
                f"[bold #5FD7AF]Attach summary[/] "
                f"[bold #F4F7FA]{len(added)} added[/] "
                f"[#7F8C9A]· {len(existing)} existing · {len(skipped)} skipped[/]"
            )
            for added_attachment in added[:5]:
                Design.console.print(
                    f"[bold #AFC7D8]  +[/] "
                    f"[bold #F4F7FA]{added_attachment.get('filename') or '-'}[/] "
                    f"[#7F8C9A]({added_attachment.get('kind') or 'file'})[/]"
                )
            if len(added) > 5:
                Design.console.print(f"[#7F8C9A]  ... and {len(added) - 5} more added[/]")
            if skipped:
                Design.console.print(
                    f"[#FFB86B]Skipped[/] "
                    f"{', '.join(str(skipped_attachment.get('filename') or '-') for skipped_attachment in skipped[:3])}"
                )
            print_attach_gap()
            continue

        if m := re_detach.match(prompt_text):
            value = m.group(1).strip() if m.group(1) else ""
            if not value:
                Design.console.print("[bold #FF5F5F]detach invalid: /detach <index|path>[/]")
                print_attach_gap()
                continue
            try:
                removed_attachment = mind.attach.remove_pending_attachment(value)
            except MindError as detach_error:
                Design.console.print(f"[bold #FF5F5F]{detach_error}[/]")
                print_attach_gap()
                continue

            Design.console.print(
                f"[bold #AFC7D8]Detached[/] "
                f"[bold #F4F7FA]{removed_attachment.get('filename') or '-'}[/]"
            )
            print_attach_gap()
            continue

        pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
        print_turn_body_gap()

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
