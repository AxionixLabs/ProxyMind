# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from loguru import logger
from engine.tinker import (
    MindError, FileAssist
)
from mind_app.mcp import McpSessionLike
from mind_app.runtime.mcp.service_runtime import prepare_and_start_service_runtime
from mind_app.runtime.support.clipboard import (
    ClipboardError,
    copy_text_to_clipboard
)
from mind_app.stream_events.failure_display import render_failure_text
from mind_nova import const
from mind_core.design import Design
from mind_nova.modes import RunMode
from mind_nova.requests import (
    build_compact_payload,
    stream_compact_events
)
from .repl_prompt import save_primary_pref_field
from .repl_tools import render_tools_summary

if typing.TYPE_CHECKING:
    from ...mind_core import Mind


class CompactLiveStatus(object):
    """记录上下文压缩的流式阶段状态。"""

    def __init__(self) -> None:
        self._message = "Context compacting..."
        self._state   = "linking"
        self._done    = False

    def snapshot(self) -> dict[str, typing.Any]:
        """返回可复用外部 MCP 动画渲染的状态快照。"""
        return {
            "summary" : self._message,
            "done"    : self._done,
            "items"   : [
                {
                    "name"  : "Compact",
                    "state" : self._state,
                }
            ]
        }

    def running(self, message: str) -> None:
        """更新压缩进行中的提示。"""
        self._message = message or "Context compacting..."
        self._state   = "linking"
        self._done    = False

    def completed(self, message: str, detail: str) -> None:
        """更新压缩完成提示。"""
        self._message = f"{message or 'Context compacted.'}{detail}"
        self._state   = "ready"
        self._done    = True

    def failed(self, message: str) -> None:
        """更新压缩失败提示。"""
        self._message = message or "Context compaction failed. Please try again."
        self._state   = "failed"
        self._done    = True


def compact_animation_enabled(mind: "Mind") -> bool:
    """判断当前日志等级是否启用压缩动画。"""
    return mind.level == const.SHOW_LEVEL


async def exchange_pref_value(
    matcher: re.Match[str],
    pref_command: typing.Literal["model", "apikey", "base-url"]
) -> typing.Optional[str]:
    """解析模型偏好类指令，并给出交互提示。"""
    if pref_command == "model":
        return matcher.group(1).strip() if matcher.group(1) else ""

    if pref_name := matcher.group(1).strip() if matcher.group(1) else None:
        return pref_name

    styles: list[str] = []

    match pref_command:
        case "model":
            styles = ["<model> (Model name or ID)"]
        case "apikey":
            styles = ["<apikey> (Provider API key)"]
        case "base-url":
            styles = ["<url> (Provider base URL)"]

    for style in styles:
        Design.console.print(f"[bold #AFC7D8]  • {style}[/]")
    Design.console.print(
        f"[bold #FF5F5F]\n {pref_command} invalid: /{pref_command} {const.ERR}{pref_name}"
    )
    Design.console.print()
    return None


async def persist_primary_pref(
    mind: "Mind",
    *,
    command_name: typing.Literal["model", "apikey", "base-url"],
    field_name: typing.Literal["model", "apikey", "base_url"],
    field_value: str
) -> typing.Optional[dict[str, typing.Any]]:
    """把 REPL 偏好命令写入 primary slot，并刷新本地缓存。"""
    try:
        saved = await save_primary_pref_field(field_name, field_value)
        await mind.refresh_pref_if_stale(ttl_sec=0.0)
    except (OSError, TypeError, ValueError) as pref_save_error:
        Design.console.print(
            f"[bold #FF5F5F]{command_name} save failed: "
            f"{type(pref_save_error).__name__}: {pref_save_error}[/]"
        )
        Design.console.print()
        return None

    saved_primary = saved.get("primary") if isinstance(saved, dict) else {}
    return saved_primary if isinstance(saved_primary, dict) else {}


async def compact_current_conversation(
    mind: "Mind",
    *,
    run_mode: RunMode,
    pref_config: dict[str, typing.Any]
) -> None:
    """压缩当前会话上下文。"""
    metadata = mind.conversation.snapshot()

    payload = build_compact_payload({
        "mode"     : run_mode,
        "cid"      : metadata["cid"],
        "sid"      : metadata["sid"],
        "llm_conf" : pref_config,
        "strategy" : "memento"
    })

    animation_running = False
    received          = False
    status            = CompactLiveStatus()
    animation_enabled = compact_animation_enabled(mind)

    if animation_enabled:
        logger.debug(
            f"[Compact] animation start "
            f"level={mind.level} cid={metadata['cid']} sid={metadata['sid']}"
        )
        await mind.start_external_mcp_anim(status.snapshot)
        animation_running = True

    try:
        async for event in stream_compact_events(payload):
            received   = True
            event_type = str(event.get("type") or "")
            message    = str(event.get("message") or "").strip()

            if event_type == "conversation.compact.started":
                status.running(message)
                logger.debug(f"[Compact] started message={status.snapshot()['summary']}")
                continue

            if event_type == "conversation.compact.failed":
                status.failed(message)
                logger.debug(f"[Compact] failed message={status.snapshot()['summary']}")
                if animation_running:
                    await mind.await_cleanup(mind.stop_anim())
                    animation_running = False
                return None

            if event_type == "conversation.compact":
                detail = compact_event_detail(event)
                status.completed(message, detail)
                logger.debug(f"[Compact] completed message={status.snapshot()['summary']}")
                if animation_running:
                    await mind.await_cleanup(mind.stop_anim())
                    animation_running = False
                return None

        if not received:
            status.failed("Context compaction failed. Please try again.")
            logger.debug(f"[Compact] failed message={status.snapshot()['summary']}")
            if animation_running:
                await mind.await_cleanup(mind.stop_anim())
                animation_running = False

    finally:
        if animation_running:
            await mind.await_cleanup(mind.stop_anim())


def compact_event_detail(event: dict[str, typing.Any]) -> str:
    """返回压缩完成事件的简短统计。"""
    before_items = event.get("before_items")
    after_items  = event.get("after_items")
    if isinstance(before_items, int) and isinstance(after_items, int):
        return f" · {before_items} -> {after_items} items"
    return ""


def print_pending_attachments(mind: "Mind") -> None:
    """打印当前待发送附件列表。"""
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


def print_attach_gap() -> None:
    Design.console.print()


async def copy_last_assistant_reply(mind: "Mind") -> None:
    """复制最近一次模型回复到剪贴板。"""
    text = mind.last_assistant_reply_snapshot()
    if not text:
        Design.console.print("[bold #7F8C9A]No assistant message to copy.[/]")
        Design.console.print()
        return None

    try:
        await copy_text_to_clipboard(text)
    except ClipboardError as error:
        Design.console.print(f"[bold #FF5F5F]Copy failed: {error}[/]")
        Design.console.print()
        return None

    Design.console.print("[bold #AFC7D8]Copied last message to clipboard[/]")
    Design.console.print()


async def link_helix_runtime(mind: "Mind") -> None:
    """确认本地服务已经启动，并挂载到当前工具会话。"""
    try:
        helix_linked = await prepare_and_start_service_runtime(mind)
    except MindError as error:
        Design.console.print(f"[bold #FF5F5F]Helix link failed: {error}[/]")
        Design.console.print()
        return None

    if not helix_linked:
        Design.console.print("[bold #AFC7D8]Helix[/] [dim #7F8C9A]· skipped[/]")
        Design.console.print()


def unlink_helix_runtime(mind: "Mind") -> None:
    """从当前工具会话移除 Helix MCP，不停止本地服务。"""
    was_linked = mind.is_service_mcp_linked()
    mind.unlink_service_mcp()
    state = "unlinked" if was_linked else "already unlinked"
    Design.console.print(f"[bold #AFC7D8]Helix[/] [dim #7F8C9A]· {state}[/]")
    Design.console.print()


async def open_helix_home(mind: "Mind") -> None:
    """启动或复用本地 Helix 服务，挂载 MCP 后打开首页。"""
    try:
        helix_ready = await prepare_and_start_service_runtime(mind)
    except MindError as error:
        Design.console.print(f"[bold #FF5F5F]Helix home failed: {error}[/]")
        Design.console.print()
        return None

    if not helix_ready:
        Design.console.print("[bold #AFC7D8]Helix[/] [dim #7F8C9A]· skipped[/]")
        Design.console.print()
        return None

    url = const.BASE_URL.rstrip("/")
    Design.console.print(f"[bold #AFC7D8]Helix Home[/] [dim #7F8C9A]· {url}[/]")
    await FileAssist.open_url(url)
    Design.console.print()


async def print_available_tools(
    mind: "Mind",
    *,
    run_mode: RunMode,
    pref_config: dict[str, typing.Any]
) -> None:
    """建立一次 MCP 会话并打印当前模式可见工具。"""
    async def render_tools_with_session(
        session: McpSessionLike,
        tools: list[dict[str, typing.Any]],
    ) -> None:
        _ = session

        render_tools_summary(
            mode=run_mode,
            tools=tools,
        )

    try:
        await mind.with_mcp_session(pref_config, render_tools_with_session)

    except (KeyboardInterrupt, SystemExit):
        raise

    except BaseException as tool_error:
        message = str(tool_error).strip()
        error   = f"{type(tool_error).__name__}: {message}" if message else type(tool_error).__name__

        Design.console.print(render_failure_text("tools.failed", error))
        Design.console.print()


if __name__ == '__main__':
    pass
