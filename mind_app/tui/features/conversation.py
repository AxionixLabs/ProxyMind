# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from mind_app.frontend import ApplicationView
from engine.observability import (
    observe,
    observe_exception
)
from mind_app.presentation.mcp_status import render_mcp_status_block
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan
)
from mind_app.runtime.support.clipboard import (
    ClipboardError,
    copy_text_to_clipboard
)
from mind_core.mcp_status import external_mcp_status_view
from mind_nova.modes import RunMode
from mind_nova.requests.compact import (
    build_compact_payload,
    stream_compact_events
)
from mind_nova.requests.fork import (
    ConversationForkRequestError,
    request_conversation_fork
)

from ..core.models import FragmentBlock
from ..core.styles import (
    ACCENT_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    WARNING_STYLE,
    fragment_block,
    text_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind


def _present(
    mind: "Mind",
    renderable: FragmentBlock | StyledBlock | None = None,
    *,
    view_type: str = "tui.command",
) -> None:
    """发送一项会话功能展示。"""
    mind.frontend.application.emit(ApplicationView(
        type=view_type,
        renderable=renderable,
    ))


def render_compact_result(mind: "Mind", status: "CompactLiveStatus") -> None:
    """展示上下文压缩的最终状态。"""
    view  = external_mcp_status_view(status.snapshot(), detail_limit=0)
    block = render_mcp_status_block(view)

    if not block.plain_text:
        return None

    _present(mind, block, view_type="tui.compact.status")
    _present(mind, view_type="tui.gap")


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
                    "name": "Compact",
                    "state": self._state,
                }
            ],
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


class ForkLiveStatus(object):
    """记录会话分支操作的阶段状态。"""

    def __init__(self) -> None:
        self._message = "Forking conversation..."
        self._state   = "linking"
        self._done    = False

    def snapshot(self) -> dict[str, typing.Any]:
        """返回复用对话操作动画的状态快照。"""
        return {
            "summary": self._message,
            "done": self._done,
            "detail_limit": 0,
            "items": [
                {
                    "name": "Fork",
                    "state": self._state,
                }
            ],
        }

    def completed(self, copied_items: int) -> None:
        """更新分支创建完成状态。"""
        suffix = f" · {copied_items} items" if copied_items > 0 else ""

        self._message = f"Conversation forked.{suffix}"
        self._state   = "ready"
        self._done    = True

    def failed(self, message: str) -> None:
        """更新分支创建失败状态。"""
        self._message = message or "Conversation fork failed. Try /fork again."
        self._state   = "failed"
        self._done    = True


def compact_animation_enabled(mind: "Mind") -> bool:
    """返回当前运行是否启用压缩动画。"""
    return mind.animate


async def compact_current_conversation(
    mind: "Mind",
    *,
    run_mode: RunMode,
    pref_config: dict[str, typing.Any],
) -> CompactLiveStatus:
    """压缩当前会话上下文。"""
    metadata = mind.conversation.snapshot()

    payload = build_compact_payload({
        "mode": run_mode,
        "cid": metadata["cid"],
        "sid": metadata["sid"],
        "llm_conf": pref_config,
        "strategy": "memento",
    })

    terminal_event: bool = False

    status = CompactLiveStatus()
    observe(
        "compact.start",
        mode=run_mode,
        cid=metadata["cid"],
        sid=metadata["sid"],
    )

    try:
        if compact_animation_enabled(mind):
            observe("compact.animation.start")
            await mind.start_compact_anim(status.snapshot)

        async for event in stream_compact_events(payload):
            event_type = str(event.get("type") or "")
            message    = str(event.get("message") or "").strip()

            if event_type == "conversation.compact.started":
                status.running(message)
                observe("compact.remote.started")
                continue

            if event_type == "conversation.compact.failed":
                status.failed(message)
                terminal_event = True
                observe("compact.failed", level="ERROR", reason=message or "remote_failed")
                break

            if event_type == "conversation.compact":
                status.completed(message, compact_event_detail(event))
                terminal_event = True
                observe(
                    "compact.complete",
                    before_items=event.get("before_items"),
                    after_items=event.get("after_items"),
                )
                break

        if not terminal_event:
            status.failed("Context compaction failed. Please try again.")
            observe("compact.failed", level="ERROR", reason="missing_terminal_event")
    except asyncio.CancelledError:
        observe("compact.interrupted", level="WARNING")
        raise
    except Exception as error:
        message = str(error).strip()
        detail = (
            f": {type(error).__name__}: {message}"
            if message
            else f": {type(error).__name__}"
        )
        status.failed(f"Context compaction failed{detail}")
        observe_exception("compact.failed", error)

    return status


async def fork_current_conversation(
    mind: "Mind",
    *,
    run_mode: RunMode,
) -> ForkLiveStatus:
    """复制当前远端上下文并在成功后切换会话标识。"""
    source = mind.conversation.snapshot()

    request_id = mind.prepare_conversation_fork(
        run_mode,
        source["cid"],
        source["sid"],
    )
    status = ForkLiveStatus()

    observe(
        "conversation.fork.start",
        mode=run_mode,
        cid=source["cid"],
        sid=source["sid"],
        request_id=request_id,
    )

    try:
        if compact_animation_enabled(mind):
            observe("conversation.fork.animation.start")
            await mind.start_compact_anim(status.snapshot)

        result = await request_conversation_fork(
            mode=run_mode,
            cid=source["cid"],
            sid=source["sid"],
            request_id=request_id,
        )

        target_cid   = str(result.get("cid") or "").strip()
        target_sid   = str(result.get("sid") or "").strip()
        copied_items = _positive_int(result.get("copied_items"))

        bound = mind.bind_conversation(
            target_cid,
            target_sid,
            source="tui",
        )
        if bound is None:
            mind.clear_conversation_fork(
                run_mode,
                source["cid"],
                source["sid"],
                request_id,
            )

            status.failed("Conversation fork returned invalid session IDs.")

            observe(
                "conversation.fork.failed",
                level="ERROR",
                reason="invalid_target",
                request_id=request_id,
            )
            return status

        mind.clear_conversation_fork(
            run_mode,
            source["cid"],
            source["sid"],
            request_id,
        )
        status.completed(copied_items)

        observe(
            "conversation.fork.complete",
            mode=run_mode,
            source_cid=source["cid"],
            source_sid=source["sid"],
            cid=bound["cid"],
            sid=bound["sid"],
            request_id=request_id,
            copied_items=copied_items,
        )

    except asyncio.CancelledError:
        observe(
            "conversation.fork.interrupted",
            level="WARNING",
            request_id=request_id,
        )
        raise

    except ConversationForkRequestError as error:
        if not error.retryable:
            mind.clear_conversation_fork(
                run_mode,
                source["cid"],
                source["sid"],
                request_id,
            )
        status.failed(error.message)

        observe(
            "conversation.fork.failed",
            level="WARNING" if error.retryable else "ERROR",
            reason=error.code or error.status_code or "request_failed",
            request_id=request_id,
        )

    except Exception as error:
        message = str(error).strip()
        detail  = f": {message}" if message else ""

        status.failed(
            f"Conversation fork failed: {type(error).__name__}{detail}"
        )

        observe_exception(
            "conversation.fork.failed",
            error,
            request_id=request_id,
        )

    return status


async def finish_compact_activity(mind: "Mind") -> None:
    """结束上下文压缩活动状态。"""
    await mind.stop_anim("compact", settle=False)


async def finish_fork_activity(mind: "Mind") -> None:
    """结束会话分支操作复用的活动动画。"""
    await mind.stop_anim("compact", settle=False)


def render_fork_result(mind: "Mind", status: ForkLiveStatus) -> None:
    """展示会话分支操作的最终状态。"""
    view = external_mcp_status_view(status.snapshot(), detail_limit=0)

    block = render_mcp_status_block(view)
    if not block.plain_text:
        return None

    _present(mind, block, view_type="tui.fork.status")
    _present(mind, view_type="tui.gap")


def render_fork_failure(mind: "Mind", error: BaseException) -> None:
    """展示会话分支操作的未处理失败。"""
    message = str(getattr(error, "message", "") or str(error)).strip()

    status = ForkLiveStatus()
    status.failed(message or "Conversation fork failed. Try /fork again.")

    render_fork_result(mind, status)


def render_fork_interrupted(mind: "Mind") -> None:
    """展示会话分支操作被中断的状态。"""
    _present(
        mind,
        fragment_block(
            TextSpan("Conversation fork ", ACCENT_STYLE),
            TextSpan("· interrupted", WARNING_STYLE),
        ),
        view_type="tui.fork.interrupted",
    )
    _present(mind, view_type="tui.gap")


def render_compact_failure(mind: "Mind", error: BaseException) -> None:
    """展示上下文压缩未处理异常的最终状态。"""
    message = str(error).strip()

    detail = (
        f": {type(error).__name__}: {message}"
        if message
        else f": {type(error).__name__}"
    )

    status = CompactLiveStatus()
    status.failed(f"Context compaction failed{detail}")

    render_compact_result(mind, status)


def render_compact_interrupted(mind: "Mind") -> None:
    """展示上下文压缩被用户中断的状态。"""
    _present(
        mind,
        fragment_block(
            TextSpan("Context compaction ", ACCENT_STYLE),
            TextSpan("· interrupted", WARNING_STYLE),
        ),
        view_type="tui.compact.interrupted",
    )
    _present(mind, view_type="tui.gap")


def compact_event_detail(event: dict[str, typing.Any]) -> str:
    """返回压缩完成事件的简短统计。"""
    before_items = event.get("before_items")
    after_items  = event.get("after_items")

    if isinstance(before_items, int) and isinstance(after_items, int):
        return f" · {before_items} -> {after_items} items"
    return ""


def _positive_int(value: typing.Any) -> int:
    """把正整数统计值规范化为非负整数。"""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


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
        _present(mind, text_block(f"Copy failed: {error}", FAILURE_STYLE))
        _present(mind, view_type="tui.gap")
        return None

    _present(mind, text_block("Copied last message to clipboard", ACCENT_STYLE))
    _present(mind, view_type="tui.gap")


if __name__ == '__main__':
    pass
