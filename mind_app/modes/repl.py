# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from mind_app.mcp import McpSessionLike
from engine.tinker import MindError
from mind_core.design import Design
from mind_core.design.upload import UploadProgressLiveReporter
from mind_nova.events import EventReport
from mind_nova.modes import (
    DEFAULT_RUN_MODE, RunMode
)
from mind_nova import const
from ..runtime.calling import resolve_mode_runner

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

    def print_attach_gap() -> None:
        Design.console.print()

    def print_turn_body_gap() -> None:
        Design.console.print()

    def print_pending_attachments() -> None:
        attachments = mind.attach.pending_attachments_snapshot()
        if not attachments:
            Design.console.print("[bold #7F8C9A]No pending attachments.[/]")
            print_attach_gap()
            return None

        Design.console.print(f"[bold #AFC7D8]Pending attachments ({len(attachments)}):[/]")
        for index, attachment in enumerate(attachments, start=1):
            size = int(attachment.get("size") or 0)
            Design.console.print(
                f"[bold #AFC7D8]  {index}.[/] "
                f"[bold #F4F7FA]{attachment.get('filename') or '-'}[/] "
                f"[#7F8C9A]({attachment.get('kind') or 'file'} · {size} bytes)[/]"
            )
            Design.console.print(f"[#7F8C9A]     {attachment.get('local') or '-'}[/]")
        print_attach_gap()

    async def exchange(
        matcher: re.Match[str],
        types: typing.Literal["model", "apikey"]
    ) -> typing.Optional[str]:
        """解析 `/model` 与 `/apikey` 指令，并给出交互提示。"""
        if pref_name := matcher.group(1).strip() if matcher.group(1) else None:
            return pref_name

        styles: list[str] = []

        match types:
            case "model":
                styles = ["<model> (Model name or ID)"]
            case "apikey":
                styles = ["<apikey> (Provider API key)"]

        for s in styles:
            Design.console.print(f"[bold #AFC7D8]  • {s}[/]")
        Design.console.print(f"[bold #FF5F5F]\n {types} invalid: /{types} {const.ERR}{pref_name}")
        Design.console.print()
        return None

    async def run_model_turn(
        message_text: str,
        run_mode: RunMode,
        turn_pref_config: dict[str, typing.Any]
    ) -> None:
        """为单轮用户输入建立 MCP 会话并执行模型流程。"""
        async def function(
            session: McpSessionLike,
            openai_tools: list[dict[str, typing.Any]],
            tool_meta: dict[str, dict[str, typing.Any]],
        ) -> None:
            """在当前 MCP 会话中执行一轮模型请求。"""
            runner = resolve_mode_runner(mind, run_mode)
            uploaded_attachments: typing.Optional[list[dict[str, typing.Any]]] = None

            if run_mode == "plan" and mind.attach.has_pending_attachments():
                Design.console.print(
                    "[bold #FF5F5F]Pending attachments are not supported in /plan. "
                    "Switch to /chat, /fast, or /xtra, or run /attach-clear.[/]"
                )
                print_attach_gap()
                return None

            if mind.attach.has_pending_attachments():
                attachments = mind.attach.pending_attachments_snapshot()
                reporter    = UploadProgressLiveReporter(Design.console)

                upload_state: dict[str, typing.Any] = {
                    "event"       : None,
                    "item_total"  : len(attachments),
                    "total_bytes" : sum(int(attachment.get("size") or 0) for attachment in attachments)
                }

                async def capture_progress(event: dict[str, typing.Any]) -> None:
                    reporter.last_event = dict(event)
                    upload_state["event"] = dict(event)

                try:
                    await mind.start_upload_anim(lambda: dict(upload_state))
                    uploaded_attachments = await mind.attach.upload_pending_attachments(
                        progress_callback=capture_progress
                    )
                except MindError as upload_error:
                    Design.console.print(
                        reporter.render_failure(message=str(upload_error), event=reporter.last_event)
                    )
                    print_attach_gap()
                    return None
                finally:
                    await mind.await_cleanup(mind.stop_anim())

                if reporter.last_event is not None:
                    Design.console.print(reporter.render_summary(reporter.last_event))
                    print_attach_gap()

            metadata  = mind.begin_session()
            ev_report = EventReport(run_mode, metadata["cid"], metadata["sid"])

            await ev_report.open()

            try:
                await mind.with_mcp_guard(
                    runner,
                    mode=run_mode,
                    session=session,
                    pref_config=turn_pref_config,
                    message=message_text,
                    openai_tools=openai_tools,
                    tool_meta=tool_meta,
                    attachments=uploaded_attachments,
                    metadata=metadata,
                    ev_report=ev_report
                )
            finally:
                await ev_report.flush()
                await ev_report.close()
                if uploaded_attachments:
                    mind.attach.clear_pending_attachments()

        await mind.with_mcp_session(turn_pref_config, function)

    quit_set: set[str] = {"/quit", "/q", "quit", "exit"}
    help_set: set[str] = {"/help", "/h"}
    seal_set: set[str] = {"/license", "/lic"}

    attachments_set: set[str]  = {"/attachments"}
    attach_clear_set: set[str] = {"/attach-clear"}
    reboot_set: set[str]       = {"/reboot"}

    doc = """\
        [bold]
        [bold #AFD7FF]/help, /h[/]                 指令索引（用法/示例/约定）
        [bold #5FD7AF]/license, /lic[/]            授权许可（License/特性）
        [bold #FF5F5F]/quit, /q, quit, exit[/]     断开会话（安全退出）
        [bold #AFD7FF]/attach <path|dir|glob>[/]   添加本轮待发送附件（任意文件）
        [bold #AFD7FF]/attachments[/]              查看当前待发送附件
        [bold #AFD7FF]/detach <index|path>[/]      移除一个待发送附件
        [bold #AFD7FF]/attach-clear[/]             清空当前待发送附件
        [bold #AFD7FF]/reboot[/]                   重启本地后台服务
        [bold #FFD75F]/chat[/]                     对话模式（交互能力协作/自然语言交互）
        [bold #FFD75F]/fast[/]                     高速模式（高吞吐任务流/数据媒体直达）
        [bold #FFD75F]/plan[/]                     编排模式（结构任务拆解/确定路径执行）
        [bold #FFD75F]/xtra[/]                     外接模式（外部 MCP 工具 + 通用工具 + 编码工具）
        [bold #7F8C9A]/model <name>[/]             引擎切换（选择推理内核）
        [bold #7F8C9A]/apikey <key>[/]             凭证更新（替换访问密钥）
        [/]"""

    re_model  = re.compile(r"^\s*/model(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_apikey = re.compile(r"^\s*/apikey(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_attach = re.compile(r"^\s*/attach(?:\s+(.*))?\s*$", re.IGNORECASE)
    re_detach = re.compile(r"^\s*/detach(?:\s+(.*))?\s*$", re.IGNORECASE)

    pref_config = await mind.fresh_pref_config()
    primary     = pref_config.get("primary") or {}
    model       = primary.get("model", "")

    mode: RunMode = DEFAULT_RUN_MODE

    while not mind.task_event.is_set():
        pref_config = await mind.fresh_pref_config()
        primary     = pref_config.get("primary") or {}
        model       = primary.get("model", "") or model

        try:
            raw = await mind.prompt_box.prompt_async(mode=mode, model=model)
        except KeyboardInterrupt:
            mind.exit_code = 130
            mind.task_event.set()
            break
        except (EOFError, UnicodeDecodeError):
            continue

        command = raw.strip().lower()

        if command in quit_set:
            mind.task_event.set()
            break

        if command in help_set:
            Design.console.print(doc)
            continue

        if command in seal_set:
            Design.startup_logo()
            continue

        if command in attachments_set:
            print_pending_attachments()
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
            Design.console.print()
            continue

        if command in MODE_BY_COMMAND:
            Design.console.print()
            mode = MODE_BY_COMMAND[command]
            continue

        if m := re_model.match(raw):
            model = await exchange(m, types="model") or model
            continue

        if m := re_apikey.match(raw):
            await exchange(m, types="apikey")
            continue

        if m := re_attach.match(raw):
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

        if m := re_detach.match(raw):
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
        await run_model_turn(raw, mode, pref_config)

    return None


if __name__ == '__main__':
    pass
