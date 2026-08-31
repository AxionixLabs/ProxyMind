# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from agent.protocol import ConversationForkReceipt
from agent.ports import (
    ProtocolCommandClient,
    ProtocolCommandError,
)
from mind_app.presentation.application import ApplicationView
from observability import (
    observe,
    observe_exception
)
from mind_app.presentation.mcp_status import (
    external_mcp_status_view,
    render_mcp_status_block,
)
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan
)
from frontends.tui.adapters.clipboard import (
    ClipboardError,
    copy_text_to_clipboard
)
from protocol.schema.identifiers import valid_session_ids
from protocol.client.fork import ResubmittablePrompt
from metadata import const
from agent.application.turns.compact_result import CompactResult
from mind_app.runtime.compaction import (
    compact_conversation
)

from ..core.models import FragmentBlock
from ..core.models import (
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..core.styles import (
    BODY_STYLE,
    BRIGHT_STYLE,
    FAILURE_STYLE,
    command_result_block,
    failure_text_block,
    fragment_block,
    interrupted_status_block
)

PromptSource: typing.TypeAlias = typing.Literal["none", "server", "client"]

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ..runtime.ports import MenuSelectionPort


# Production uses the ProtocolCommandClient injected by RuntimeServices. This
# unbound seam keeps the existing request-level tests independent of transport.
request_conversation_fork: typing.Any = None


def _present(
    mind: "Mind",
    renderable: FragmentBlock | StyledBlock | None = None,
    *,
    view_type: str = "tui.command"
) -> None:
    """发送一项会话功能展示。"""
    mind.frontend.application.emit(ApplicationView(
        type=view_type,
        renderable=renderable,
    ))


def render_compact_result(mind: "Mind", status: "CompactLiveStatus") -> None:
    """展示上下文压缩的最终状态。"""
    view = external_mcp_status_view(status.snapshot(), detail_limit=0)

    block = render_mcp_status_block(
        view,
        terminal_width=getattr(
            getattr(mind.frontend.application, "viewport", None),
            "width",
            None,
        ),
    )

    if not block.plain_text:
        return None

    _present(mind, block, view_type="tui.compact.status")
    _present(mind, view_type="tui.gap")


async def confirm_archive_session(runtime: "MenuSelectionPort") -> bool:
    """显示当前会话归档确认菜单并返回用户是否确认。"""
    selected = await runtime.select_menu(MenuRequest(
        title="Archive this session?",
        status="Archive the current session and exit.",
        body=(
            f"Are you sure? This will archive the current session "
            f"and exit {const.APP_DESC}",
        ),
        view_id="conversation:archive-confirm",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=(
            MenuOption(
                value=False,
                label="No, don't archive",
                detail="Return to the current session",
            ),
            MenuOption(
                value=True,
                label="Yes, archive and exit",
                detail="Archive this session now",
            ),
        ),
        selected=0,
    ))
    return selected is True


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

        self._prompt: ResubmittablePrompt | None = None

        self._source_session: tuple[str, str] | None = None
        self._target_session: tuple[str, str] | None = None

    @property
    def succeeded(self) -> bool:
        """返回会话分支是否已经成功完成。"""
        return self._done and self._state == "ready"

    @property
    def prompt(self) -> ResubmittablePrompt | None:
        """返回分支轮次对应的可重提交输入。"""
        return self._prompt

    @property
    def source_session(self) -> tuple[str, str] | None:
        """返回分支操作使用的源会话游标。"""
        return self._source_session

    @property
    def target_session(self) -> tuple[str, str] | None:
        """返回分支操作创建的目标会话游标。"""
        return self._target_session

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

    def completed(
        self,
        copied_items: int,
        *,
        prompt: ResubmittablePrompt | None = None,
        source_session: tuple[str, str] | None = None,
        target_session: tuple[str, str] | None = None
    ) -> None:
        """更新分支创建完成状态。"""
        suffix = f" · {copied_items} items" if copied_items > 0 else ""

        self._message = f"Conversation forked.{suffix}"
        self._state   = "ready"
        self._done    = True

        self._prompt  = prompt

        self._source_session = source_session
        self._target_session = target_session

    def failed(self, message: str) -> None:
        """更新分支创建失败状态。"""
        self._message = message or "Conversation fork failed. Try /fork again."
        self._state   = "failed"
        self._done    = True

    def created_empty(
        self,
        *,
        source_session: tuple[str, str],
        target_session: tuple[str, str],
    ) -> None:
        """记录空来源直接切换到新会话的结果。"""
        self._message = "New conversation started."
        self._state   = "ready"
        self._done    = True

        self._source_session = source_session
        self._target_session = target_session


async def _replace_empty_fork_source(
    mind: "Mind",
    status: ForkLiveStatus,
    source: dict[str, str],
    *,
    event: str,
    request_id: str = "",
) -> ForkLiveStatus:
    """把没有远端历史的分支请求转换为新会话。"""
    target = await mind.reset_conversation(
        reason="command:/fork-empty",
        source="tui:fork-empty",
    )
    status.created_empty(
        source_session=(source["cid"], source["sid"]),
        target_session=(target["cid"], target["sid"]),
    )
    observe(
        event,
        reason="empty_source" if not request_id else "source_missing",
        source_cid=source["cid"],
        source_sid=source["sid"],
        cid=target["cid"],
        sid=target["sid"],
        request_id=request_id,
    )
    return status


def compact_animation_enabled(mind: "Mind") -> bool:
    """返回当前运行是否启用压缩动画。"""
    return mind.animate


async def compact_current_conversation(
    mind: "Mind",
    *,
    pref_config: dict[str, typing.Any],
) -> CompactLiveStatus:
    """压缩当前会话上下文。"""
    status = CompactLiveStatus()

    if compact_animation_enabled(mind):
        observe("compact.animation.start")
        await mind.start_compact_anim(status.snapshot)

    result = await compact_conversation(
        mind,
        pref_config=pref_config,
        source="tui",
        on_progress=status.running,
    )

    if result.ok:
        status.completed(result.message, compact_result_detail(result))
    else:
        status.failed(result.message)

    return status


async def fork_current_conversation(
    mind: "Mind",
    *,
    before_turn_id: str = "",
    bind_target: bool = True,
    fallback_prompt: ResubmittablePrompt | None = None
) -> ForkLiveStatus:
    """复制完整或指定轮次之前的上下文并按需切换会话标识。"""
    source   = mind.conversation.snapshot()
    boundary = str(before_turn_id or "").strip()
    status   = ForkLiveStatus()

    if (
        bind_target
        and not boundary
        and not mind.conversation.fork_source_available
    ):
        return await _replace_empty_fork_source(
            mind,
            status,
            source,
            event="conversation.fork.skipped",
        )

    request_id = mind.prepare_conversation_fork(
        source["cid"],
        source["sid"],
        boundary,
    )
    prompt_source: PromptSource = "none"
    if boundary:
        prompt_source = "server" if fallback_prompt is None else "client"

    observe(
        "conversation.fork.start",
        cid=source["cid"],
        sid=source["sid"],
        request_id=request_id,
        before_turn_id=boundary,
    )

    try:
        if compact_animation_enabled(mind):
            observe("conversation.fork.animation.start")
            await mind.start_compact_anim(status.snapshot)

        protocol_client = _protocol_client_for(mind)
        if protocol_client is not None:
            receipt = await protocol_client.fork_session(
                cid=source["cid"],
                sid=source["sid"],
                request_id=request_id,
                prompt_source=prompt_source,
                before_turn_id=boundary or None,
            )
            result = _fork_receipt_values(receipt)
        else:
            if not callable(request_conversation_fork):
                raise RuntimeError(
                    "TUI conversation fork requires ProtocolCommandClient"
                )
            result = await request_conversation_fork(
                cid=source["cid"],
                sid=source["sid"],
                request_id=request_id,
                prompt_source=prompt_source,
                before_turn_id=boundary or None,
            )

        target_cid   = str(result.get("cid") or "").strip()
        target_sid   = str(result.get("sid") or "").strip()
        copied_items = _positive_int(result.get("copied_items"))
        prompt       = result.get("prompt")

        if boundary and not isinstance(prompt, ResubmittablePrompt):
            prompt = fallback_prompt

        if boundary and not isinstance(prompt, ResubmittablePrompt):
            mind.clear_conversation_fork(
                source["cid"],
                source["sid"],
                request_id,
                boundary,
            )
            status.failed("Conversation fork returned an invalid prompt.")
            observe(
                "conversation.fork.failed",
                level="ERROR",
                reason="invalid_prompt",
                request_id=request_id,
            )
            return status

        if not valid_session_ids(target_cid, target_sid):
            mind.clear_conversation_fork(
                source["cid"],
                source["sid"],
                request_id,
                boundary,
            )

            status.failed("Conversation fork returned invalid session IDs.")

            observe(
                "conversation.fork.failed",
                level="ERROR",
                reason="invalid_target",
                request_id=request_id,
            )
            return status

        bound_cid = target_cid
        bound_sid = target_sid

        if bind_target:
            bound = await mind.bind_conversation(
                target_cid,
                target_sid,
                source="tui",
            )
            if bound is None:
                mind.clear_conversation_fork(
                    source["cid"],
                    source["sid"],
                    request_id,
                    boundary,
                )

                status.failed("Conversation fork returned invalid session IDs.")

                observe(
                    "conversation.fork.failed",
                    level="ERROR",
                    reason="invalid_target",
                    request_id=request_id,
                )
                return status

            bound_cid = bound["cid"]
            bound_sid = bound["sid"]

        mind.clear_conversation_fork(
            source["cid"],
            source["sid"],
            request_id,
            boundary,
        )

        status.completed(
            copied_items,
            prompt=prompt if isinstance(prompt, ResubmittablePrompt) else None,
            source_session=(source["cid"], source["sid"]),
            target_session=(bound_cid, bound_sid),
        )

        observe(
            "conversation.fork.complete",
            source_cid=source["cid"],
            source_sid=source["sid"],
            cid=bound_cid,
            sid=bound_sid,
            request_id=request_id,
            copied_items=copied_items,
            before_turn_id=boundary,
        )

    except asyncio.CancelledError:
        observe(
            "conversation.fork.interrupted",
            level="WARNING",
            request_id=request_id,
        )
        raise

    except ProtocolCommandError as error:
        error_code = error.code
        if bind_target and not boundary and error_code == "source_missing":
            mind.clear_conversation_fork(
                source["cid"],
                source["sid"],
                request_id,
                boundary,
            )
            return await _replace_empty_fork_source(
                mind,
                status,
                source,
                event="conversation.fork.recovered",
                request_id=request_id,
            )

        if not error.retryable:
            mind.clear_conversation_fork(
                source["cid"],
                source["sid"],
                request_id,
                boundary,
            )
        status.failed(error.message)

        observe(
            "conversation.fork.failed",
            level="WARNING" if error.retryable else "ERROR",
            reason=error_code or error.details.get("status_code") or "request_failed",
            request_id=request_id,
        )

    except Exception as error:
        if _protocol_client_for(mind) is None:
            error_code = str(getattr(error, "code", "") or "").strip()
            if bind_target and not boundary and error_code == "source_missing":
                mind.clear_conversation_fork(
                    source["cid"],
                    source["sid"],
                    request_id,
                    boundary,
                )
                return await _replace_empty_fork_source(
                    mind,
                    status,
                    source,
                    event="conversation.fork.recovered",
                    request_id=request_id,
                )
            if hasattr(error, "retryable"):
                retryable = bool(getattr(error, "retryable", False))
                if not retryable:
                    mind.clear_conversation_fork(
                        source["cid"],
                        source["sid"],
                        request_id,
                        boundary,
                    )
                message = str(
                    getattr(error, "message", "") or str(error)
                ).strip()
                status.failed(message)
                observe(
                    "conversation.fork.failed",
                    level="WARNING" if retryable else "ERROR",
                    reason=(
                        error_code
                        or getattr(error, "status_code", 0)
                        or "request_failed"
                    ),
                    request_id=request_id,
                )
                return status
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


def _protocol_client_for(mind: "Mind") -> ProtocolCommandClient | None:
    """读取 TUI 使用的远端 Protocol Client。"""
    services = getattr(mind, "runtime_services", None)
    candidate = getattr(services, "model_capability", None)
    return candidate if isinstance(candidate, ProtocolCommandClient) else None


def _fork_receipt_values(receipt: ConversationForkReceipt) -> dict[str, typing.Any]:
    """把协议分支回执转换为 TUI 展示层所需的稳定字段。"""
    values: dict[str, typing.Any] = {
        "request_id": receipt.request_id,
        "source_cid": receipt.source_cid,
        "source_sid": receipt.source_sid,
        "prompt_source": receipt.prompt_source,
        "cid": receipt.cid,
        "sid": receipt.sid,
        "copied_items": receipt.copied_items,
    }
    if receipt.copied_turns is not None:
        values["copied_turns"] = receipt.copied_turns
    if receipt.before_turn_id is not None:
        values["before_turn_id"] = receipt.before_turn_id
    if receipt.prompt is not None:
        values["prompt"] = ResubmittablePrompt(
            message=receipt.prompt.message,
            attachments=tuple(
                dict(item) for item in receipt.prompt.attachments
            ),
            extras=dict(receipt.prompt.extras),
        )
    return values


def render_fork_result(mind: "Mind", status: ForkLiveStatus) -> None:
    """展示会话分支操作的最终状态。"""
    view = external_mcp_status_view(status.snapshot(), detail_limit=0)

    block = render_mcp_status_block(
        view,
        terminal_width=getattr(
            getattr(mind.frontend.application, "viewport", None),
            "width",
            None,
        ),
    )
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
        interrupted_status_block("Conversation fork"),
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
        interrupted_status_block("Context compaction"),
        view_type="tui.compact.interrupted",
    )
    _present(mind, view_type="tui.gap")


def compact_result_detail(result: CompactResult) -> str:
    """返回压缩完成事件的简短统计。"""
    before_items = result.before_items
    after_items  = result.after_items

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
        _present(mind, failure_text_block("No agent response to copy"))
        _present(mind, view_type="tui.gap")
        return None

    try:
        await copy_text_to_clipboard(text)
    except ClipboardError as error:
        _present(mind, command_result_block(
            "/copy",
            TextSpan(f"Failed: {error}", FAILURE_STYLE),
        ))
        _present(mind, view_type="tui.gap")
        return None

    _present(mind, fragment_block(
        TextSpan("• ", BODY_STYLE),
        TextSpan("Copied last message to clipboard", BRIGHT_STYLE),
    ))
    _present(mind, view_type="tui.gap")


if __name__ == '__main__':
    pass
