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
from .support.repl_commands import (
    exchange_pref_value,
    open_pref_page,
    persist_primary_pref,
    print_attach_gap,
    print_available_tools,
    print_pending_attachments
)
from .support.repl_prompt import (
    WORKSPACE_LABEL_REFRESH,
    fetch_runtime_workspace_root,
    ignored_repl_input,
    primary_model_from_config,
    workspace_display_label
)
from .support.repl_mcp import render_mcp_status
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
    reboot_set: set[str]       = {"/reboot"}
    resume_set: set[str]       = {"/resume"}
    pref_set: set[str]         = {"/pref"}
    tools_set: set[str]        = {"/tools"}
    shutdown_set: set[str]     = {"/shutdown"}

    doc = """\
        [bold]
        [bold #AFD7FF]/help, /h[/]                 指令索引（用法/示例/约定）
        [bold #5FD7AF]/license, /lic[/]            授权许可（License/特性）
        [bold #AFD7FF]/new[/]                      开始新对话（保留模式、模型和待发送附件）
        [bold #AFD7FF]/resume[/]                   从当前模式最近 24 小时会话中恢复
        [bold #FF5F5F]/quit, /q, quit, exit[/]     断开会话（安全退出）
        [bold #AFD7FF]/attach <path|dir|glob>[/]   添加本轮待发送附件（任意文件）
        [bold #AFD7FF]/attachments[/]              查看当前待发送附件
        [bold #AFD7FF]/detach <index|path>[/]      移除一个待发送附件
        [bold #AFD7FF]/attach-clear[/]             清空当前待发送附件
        [bold #AFD7FF]/reboot[/]                   重启本地后台服务
        [bold #FF5F5F]/shutdown[/]                 关闭前台并停止本地运行时
        [bold #AFD7FF]/pref[/]                     打开偏好配置页
        [bold #AFD7FF]/tools[/]                    查看当前可用 MCP 工具
        [bold #FFD75F]/chat[/]                     对话模式（交互能力协作/自然语言交互）
        [bold #FFD75F]/fast[/]                     高速模式（高吞吐任务流/数据媒体直达）
        [bold #FFD75F]/plan[/]                     编排模式（结构任务拆解/确定路径执行）
        [bold #FFD75F]/xtra[/]                     外接模式（外部 MCP 工具 + 通用工具 + 编码工具）
        [bold #7F8C9A]/model <name>[/]             持久化主模型名称
        [bold #7F8C9A]/apikey <key>[/]             持久化主模型访问密钥
        [bold #7F8C9A]/base-url <url>[/]           持久化主模型 Base URL
        [/]"""

    re_model    = re.compile(r"^\s*/model(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_apikey   = re.compile(r"^\s*/apikey(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_base_url = re.compile(r"^\s*/base-url(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_attach   = re.compile(r"^\s*/attach(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_detach   = re.compile(r"^\s*/detach(?:\s+(.*))?\s*$", re.IGNORECASE)

    pref_config = await mind.fresh_pref_config()
    primary     = pref_config.get("primary") or {}
    model       = primary.get("model", "")

    mode: RunMode = DEFAULT_RUN_MODE

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

            workspace_label = workspace_display_label(runtime_workspace_root)
            workspace_label_refreshed_at = now

        try:
            prompt_text = await mind.prompt_box.prompt_async(
                mode=mode,
                model=model,
                workspace_label=workspace_label
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

        if command in reboot_set:
            Design.console.print("[bold #AFC7D8]Runtime[/] [dim #7F8C9A]· reboot[/]")
            try:
                await mind.reboot_runtime()
            except MindError as error:
                Design.console.print(f"[bold #FF5F5F]Runtime reboot failed: {error}[/]")
                Design.console.print()
                continue
            workspace_label_refreshed_at = 0.0
            Design.console.print()
            continue

        if command in shutdown_set:
            mind.stop_runtime_on_exit = True
            mind.task_event.set()
            Design.console.print("[bold #AFC7D8]Shutdown[/] [dim #7F8C9A]· stop backend runtime[/]")
            Design.console.print()
            break

        if command in pref_set:
            await open_pref_page()
            continue

        if command in tools_set:
            pref_config = await mind.fresh_pref_config(ttl_sec=0.0)
            await print_available_tools(mind, run_mode=mode, pref_config=pref_config)
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

        if m := re_model.match(prompt_text):
            if model_value := await exchange_pref_value(m, pref_command="model"):
                saved_primary = await persist_primary_pref(
                    mind,
                    command_name="model",
                    field_name="model",
                    field_value=model_value
                )
                if saved_primary is not None:
                    model = str(saved_primary.get("model") or model_value)
                    Design.console.print(
                        f"[bold #AFC7D8]Model saved[/] "
                        f"[bold #F4F7FA]{model}[/]"
                    )
                    Design.console.print()
            continue

        if m := re_apikey.match(prompt_text):
            if api_key_value := await exchange_pref_value(m, pref_command="apikey"):
                saved_primary = await persist_primary_pref(
                    mind,
                    command_name="apikey",
                    field_name="apikey",
                    field_value=api_key_value
                )
                if saved_primary is not None:
                    tail = str(saved_primary.get("apikey") or api_key_value)[-6:]
                    Design.console.print(
                        f"[bold #AFC7D8]API key saved[/] "
                        f"[dim #7F8C9A]tail=...{tail}[/]"
                    )
                    Design.console.print()
            continue

        if m := re_base_url.match(prompt_text):
            if base_url_value := await exchange_pref_value(m, pref_command="base-url"):
                saved_primary = await persist_primary_pref(
                    mind,
                    command_name="base-url",
                    field_name="base_url",
                    field_value=base_url_value
                )
                if saved_primary is not None:
                    base_url = str(saved_primary.get("base_url") or base_url_value)
                    Design.console.print(
                        f"[bold #AFC7D8]Base URL saved[/] "
                        f"[bold #F4F7FA]{base_url}[/]"
                    )
                    Design.console.print()
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
            pref_config=pref_config
        )

    return None


if __name__ == '__main__':
    pass
