# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import typing
from loguru import logger
from engine.errors import MindError
from engine.file_assist import FileAssist
from mind_app.mcp.contracts import McpSessionLike
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from mind_app.presentation.models import TextSpan
from mind_app.runtime.support.clipboard import (
    ClipboardError,
    copy_text_to_clipboard
)
from mind_app.stream_events.failure_display import render_failure_display_parts
from mind_nova import const
from mind_nova.modes import RunMode
from mind_nova.requests.compact import (
    build_compact_payload,
    stream_compact_events
)
from .context import save_primary_pref_field
from .download import prepare_tui_service_runtime
from .tools import render_tools_summary
from ..core.models import FragmentBlock
from ..core.styles import (
    ACCENT_STYLE,
    BRIGHT_STYLE,
    MUTED_STYLE,
    FAILURE_STYLE,
    fragment_block,
    text_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind


def _present(
    mind: "Mind",
    renderable: FragmentBlock | None = None,
    *,
    view_type: str = "tui.command",
) -> None:
    """发送一项 TUI 命令展示。"""
    mind.frontend.application.emit(ApplicationView(
        type=view_type,
        renderable=renderable,
    ))


def _label_detail(label: str, detail: str) -> FragmentBlock:
    """生成标题和次要详情组成的命令状态块。"""
    return fragment_block(
        TextSpan(f"{label} ", ACCENT_STYLE),
        TextSpan(f"· {detail}", MUTED_STYLE),
    )


def _failure_block(message: str) -> FragmentBlock:
    """生成单行命令错误块。"""
    return text_block(message, FAILURE_STYLE)


class CompactLiveStatus(object):
    """记录上下文压缩的流式阶段状态。"""

    def __init__(self) -> None:
        self._message = "Context compacting..."
        self._state   = "linking"
        self._done    = False

    def snapshot(self) -> dict[str, typing.Any]:
        """返回可复用外部 MCP 动画渲染的状态快照。"""
        return {
            "summary": self._message,
            "done": self._done,
            "detail_limit": 0,
            "items": [
                {
                    "name"  : "Compact",
                    "state" : self._state
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
    """返回当前运行是否启用压缩动画。"""
    return mind.animate


async def exchange_pref_value(
    application: ApplicationSink,
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
        application.emit(ApplicationView(
            type="tui.preference.help",
            renderable=text_block(f"  • {style}", ACCENT_STYLE),
        ))
    application.emit(ApplicationView(
        type="tui.preference.invalid",
        renderable=text_block(
            f"{pref_command} invalid: /{pref_command} {pref_name or ''}",
            FAILURE_STYLE,
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))
    return None


async def persist_primary_pref(
    mind: "Mind",
    *,
    command_name: typing.Literal["model", "apikey", "base-url", "model-effort"],
    field_name: typing.Literal["model", "apikey", "base_url", "reasoning_effort"],
    field_value: str
) -> typing.Optional[dict[str, typing.Any]]:
    """把 TUI 偏好命令写入 primary slot，并刷新本地缓存。"""
    try:
        saved = await save_primary_pref_field(field_name, field_value)
        await mind.refresh_pref_if_stale(ttl_sec=0.0)
    except (OSError, TypeError, ValueError) as pref_save_error:
        _present(
            mind,
            text_block(
                f"{command_name} save failed: "
                f"{type(pref_save_error).__name__}: {pref_save_error}",
                FAILURE_STYLE,
            )
        )
        _present(mind, view_type="tui.gap")
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
            f"cid={metadata['cid']} sid={metadata['sid']}"
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
                    await mind.await_cleanup(mind.stop_anim("external_mcp"))
                    animation_running = False
                return None

            if event_type == "conversation.compact":
                detail = compact_event_detail(event)
                status.completed(message, detail)
                logger.debug(f"[Compact] completed message={status.snapshot()['summary']}")
                if animation_running:
                    await mind.await_cleanup(mind.stop_anim("external_mcp"))
                    animation_running = False
                return None

        if not received:
            status.failed("Context compaction failed. Please try again.")
            logger.debug(f"[Compact] failed message={status.snapshot()['summary']}")
            if animation_running:
                await mind.await_cleanup(mind.stop_anim("external_mcp"))
                animation_running = False

    finally:
        if animation_running:
            await mind.await_cleanup(mind.stop_anim("external_mcp"))


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
        _present(mind, text_block("No pending attachments.", MUTED_STYLE))
        _present(mind, view_type="tui.gap")
        return None

    _present(
        mind,
        text_block(f"Pending attachments ({len(attachments)}):", ACCENT_STYLE),
    )
    for index, attachment in enumerate(attachments, start=1):
        size = int(attachment.get("size") or 0)
        _present(
            mind,
            fragment_block(
                TextSpan(f"  {index}. ", ACCENT_STYLE),
                TextSpan(f"{attachment.get('filename') or '-'} ", BRIGHT_STYLE),
                TextSpan(
                    f"({attachment.get('kind') or 'file'} · {size} bytes)",
                    MUTED_STYLE,
                ),
            )
        )
        _present(
            mind,
            text_block(f"     {attachment.get('local') or '-'}", MUTED_STYLE),
        )
    _present(mind, view_type="tui.gap")


async def copy_last_assistant_reply(mind: "Mind") -> None:
    """复制最近一次模型回复到剪贴板。"""
    text = mind.last_assistant_reply_snapshot()
    if not text:
        _present(mind, text_block("No assistant message to copy.", MUTED_STYLE))
        _present(mind, view_type="tui.gap")
        return None

    try:
        await copy_text_to_clipboard(text)
    except ClipboardError as error:
        _present(mind, _failure_block(f"Copy failed: {error}"))
        _present(mind, view_type="tui.gap")
        return None

    _present(mind, text_block("Copied last message to clipboard", ACCENT_STYLE))
    _present(mind, view_type="tui.gap")


async def link_helix_runtime(mind: "Mind") -> None:
    """确认本地服务已经启动，并挂载到当前工具会话。"""
    try:
        helix_linked = await prepare_tui_service_runtime(mind)
    except MindError as error:
        _present(mind, _failure_block(f"Helix link failed: {error}"))
        _present(mind, view_type="tui.gap")
        return None

    if not helix_linked:
        _present(mind, _label_detail("Helix", "skipped"))
        _present(mind, view_type="tui.gap")


def unlink_helix_runtime(mind: "Mind") -> None:
    """从当前工具会话移除 Helix MCP，不停止本地服务。"""
    was_linked = mind.is_service_mcp_linked()
    mind.unlink_service_mcp()
    state = "unlinked" if was_linked else "already unlinked"
    _present(mind, _label_detail("Helix", state))
    _present(mind, view_type="tui.gap")


async def open_helix_home(mind: "Mind") -> None:
    """启动或复用本地 Helix 服务，挂载 MCP 后打开首页。"""
    try:
        helix_ready = await prepare_tui_service_runtime(mind)
    except MindError as error:
        _present(mind, _failure_block(f"Helix home failed: {error}"))
        _present(mind, view_type="tui.gap")
        return None

    if not helix_ready:
        _present(mind, _label_detail("Helix", "skipped"))
        _present(mind, view_type="tui.gap")
        return None

    url = helix_runtime_home_url(mind)

    _present(mind, _label_detail("Helix Home", url))
    await FileAssist.open_url(url)
    _present(mind, view_type="tui.gap")


def helix_runtime_home_url(mind: "Mind") -> str:
    """返回当前 Helix 服务管理器确认的首页地址。"""
    server_manager = getattr(mind, "server_manager", None)

    url = str(getattr(server_manager, "url", "") or "").strip()

    return (url or const.BASE_URL).rstrip("/")


async def stop_helix_runtime(mind: "Mind") -> None:
    """停止 Helix 服务并打印结果。"""
    _present(mind, _label_detail("Helix", "stop"))
    try:
        await mind.stop_service_runtime()
    except MindError as error:
        _present(mind, _failure_block(f"Helix stop failed: {error}"))
        _present(mind, view_type="tui.gap")
        return None
    _present(mind, view_type="tui.gap")


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
            application=mind.frontend.application,
            mode=run_mode,
            tools=tools
        )

    try:
        await mind.with_mcp_session(pref_config, render_tools_with_session)

    except (KeyboardInterrupt, SystemExit):
        raise

    except BaseException as tool_error:
        message = str(tool_error).strip()
        error   = f"{type(tool_error).__name__}: {message}" if message else type(tool_error).__name__

        _present(
            mind,
            fragment_block(*render_failure_display_parts("tools.failed", error)),
        )
        _present(mind, view_type="tui.gap")


if __name__ == '__main__':
    pass
