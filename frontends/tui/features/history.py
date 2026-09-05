# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from prompt_toolkit.utils import get_cwidth

from agent.application.views import uses_native_tool_view
from agent.application.views.builders.tools import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view
)
from agent.domain.transcripts import (
    TranscriptEntry,
    TranscriptReplay,
)
from agent.protocol.json_value import ThawedJsonValue
from agent.stores.sessions import normalize_workspace
from frontends.terminal.capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities
)
from frontends.terminal.renderers.dispatch import (
    render_presentation_raw_view,
    render_presentation_transcript_view,
    render_presentation_view
)
from frontends.terminal.text import (
    sanitize_terminal_line,
    sanitize_terminal_text
)
from frontends.tui.contracts.resume import (
    ResumeDensity,
    ResumeFilterMode,
    ResumeLaunchContext,
    ResumePickerRequest,
    ResumePreview,
    ResumePreviewLoader,
    ResumePreviewStatus,
    ResumeRow,
    ResumeSessionStatus,
    ResumeSortKey,
    ResumeTranscriptLoader
)
from protocol.schema.identifiers import valid_session_ids
from ..adapters.markdown import render_tui_assistant_markdown
from ..adapters.presentation import render_presentation_fragment_block
from ..core.document import TranscriptBlock
from ..core.styles import (
    MUTED_STYLE,
    failure_text_block,
    query_block,
    styled_fragment_block,
    text_block
)

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost
    from ..runtime.ports import ResumePickerPort


@dataclass(frozen=True, slots=True)
class _TranscriptToolResult:
    """描述历史记录中已经校验的工具结果投影。"""

    ok: bool
    text: str
    data: dict[str, ThawedJsonValue]


class HistoryResumePreviewLoader(ResumePreviewLoader):
    """从本地 history transcript 生成 picker 使用的只读预览。"""

    PREVIEW_BLOCK_LIMIT: typing.Final[int] = 6

    def __init__(
        self,
        controller: "TuiApplicationHost",
        *,
        terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES,
    ) -> None:
        self._controller = controller
        self._terminal_capabilities = terminal_capabilities

    async def load(
        self,
        row: ResumeRow,
        *,
        width: int,
    ) -> ResumePreview:
        """在线程边界读取并规范化最近的用户和助手正文。"""
        blocks = await asyncio.to_thread(
            load_history_transcript,
            self._controller,
            row.sid,
            terminal_width=max(1, int(width) - 4),
            hyperlinks=False,
            terminal_capabilities=self._terminal_capabilities,
        )
        preview_blocks = tuple(
            (
                (
                    "class:resume-picker.preview.user"
                    if block.kind == "user"
                    else "class:resume-picker.preview.assistant",
                    sanitize_terminal_text(block.raw_text or ""),
                ),
            )
            for block in blocks
            if block.kind in {"user", "assistant"}
            and str(block.raw_text or "").strip()
        )[-self.PREVIEW_BLOCK_LIMIT:]
        return ResumePreview(
            row_key=row.key,
            status=ResumePreviewStatus.READY,
            blocks=preview_blocks,
        )


class HistoryResumeTranscriptLoader(ResumeTranscriptLoader):
    """从本地 history transcript 生成全屏 pager 使用的完整内容。"""

    def __init__(
        self,
        controller: "TuiApplicationHost",
        *,
        terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES
    ) -> None:
        self._controller = controller
        self._terminal_capabilities = terminal_capabilities

    async def load(
        self,
        row: ResumeRow,
        *,
        width: int,
    ) -> ResumePreview:
        """在线程边界读取全部 transcript，并保留已有渲染 fragment。"""
        blocks = await asyncio.to_thread(
            load_history_transcript,
            self._controller,
            row.sid,
            terminal_width=max(1, int(width)),
            hyperlinks=False,
            terminal_capabilities=self._terminal_capabilities,
        )
        transcript_blocks = tuple(
            tuple(block.transcript_block.fragments)
            for block in blocks
            if block.transcript_block.fragments
        )
        return ResumePreview(
            row_key=row.key,
            status=ResumePreviewStatus.READY,
            blocks=transcript_blocks,
        )


async def choose_history_session(
    runtime: "ResumePickerPort",
    records: list[dict[str, typing.Any]],
    *,
    filter_workspace: str | Path | None = None,
    show_workspace: bool = False,
    preview_loader: ResumePreviewLoader | None = None,
    transcript_loader: ResumeTranscriptLoader | None = None,
    archive_session: typing.Callable[[ResumeRow], typing.Awaitable[None]] | None = None,
    unarchive_session: typing.Callable[[ResumeRow], typing.Awaitable[ResumeRow]] | None = None
) -> dict[str, typing.Any] | None:
    """规范化历史记录，通过专用 picker 选择并映射回原始记录。"""
    record_by_key: dict[tuple[str, str], dict[str, typing.Any]] = {}

    rows: list[ResumeRow] = []

    for record in records:
        cid = str(record.get("cid") or "").strip()
        sid = str(record.get("sid") or "").strip()
        if not valid_session_ids(cid, sid):
            continue

        title = sanitize_terminal_line(record.get("title") or cid or "-") or "-"

        row = ResumeRow(
            cid=cid,
            sid=sid,
            title=title,
            workspace=sanitize_terminal_line(record.get("workspace") or ""),
            source=sanitize_terminal_line(record.get("source") or ""),
            created_at_ms=_optional_timestamp(record.get("created_at")),
            updated_at_ms=_optional_timestamp(record.get("updated_at")),
            branch=sanitize_terminal_line(
                record.get("branch") or record.get("git_branch") or ""
            ),
            status=_resume_session_status(record.get("status")),
        )
        if row.key in record_by_key:
            continue
        record_by_key[row.key] = record
        rows.append(row)

    normalized_workspace = normalize_workspace(filter_workspace)

    selected = await runtime.view_resume_picker(ResumePickerRequest(
        rows=tuple(rows),
        filter_workspace=normalized_workspace or None,
        show_workspace=show_workspace,
        initial_filter=(
            ResumeFilterMode.CWD
            if normalized_workspace
            else ResumeFilterMode.ALL
        ),
        initial_sort=ResumeSortKey.UPDATED,
        initial_density=ResumeDensity.DENSE,
        launch_context=ResumeLaunchContext.EXISTING_SESSION,
        preview_loader=preview_loader,
        transcript_loader=transcript_loader,
        archive_session=archive_session,
        unarchive_session=unarchive_session,
    ))
    if selected is None:
        return None
    record = record_by_key.get(selected.key)
    if record is None:
        return None
    if selected.status is ResumeSessionStatus.ACTIVE:
        record = dict(record)
        record["status"] = ResumeSessionStatus.ACTIVE.value
    return record


def _optional_timestamp(value: typing.Any) -> int | None:
    """把缺失或非法 history 时间转换为 None。"""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _resume_session_status(value: typing.Any) -> ResumeSessionStatus:
    """把历史记录状态规范化为 Resume 可识别的枚举。"""
    if isinstance(value, ResumeSessionStatus):
        return value
    return (
        ResumeSessionStatus.ARCHIVED
        if str(value or "").strip().casefold() == "archived"
        else ResumeSessionStatus.ACTIVE
    )


def load_history_transcript(
    controller: "TuiApplicationHost",
    session_id: str,
    *,
    terminal_width: int,
    hyperlinks: bool = False,
    terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES,
    record: typing.Mapping[str, typing.Any] | None = None
) -> tuple[TranscriptBlock, ...]:
    """读取会话事件并在内容缺失时生成最小恢复块。"""
    entries = (
        entry
        for entry in controller.conversation.history.read_transcript(session_id)
        if entry.session_id == session_id
    )

    replay = TranscriptReplay(entries).build()

    blocks = _render_replay_blocks(
        replay,
        terminal_width=terminal_width,
        hyperlinks=hyperlinks,
        terminal_capabilities=terminal_capabilities,
    )

    if blocks:
        return blocks

    legacy = _legacy_record_blocks(record)
    if legacy:
        return legacy

    return (_missing_transcript_block(session_id),)


def _legacy_record_blocks(
    record: typing.Mapping[str, typing.Any] | None
) -> tuple[TranscriptBlock, ...]:
    """从旧会话游标保留的标题恢复最小用户上下文。"""
    if record is None:
        return ()

    title = str(record.get("title") or "").strip()
    if not title:
        return ()

    block = query_block(title)
    return (TranscriptBlock(
        display_block=block,
        transcript_block=block,
        kind="user",
        raw_text=title,
        prompt=title,
    ),)


def _missing_transcript_block(session_id: str) -> TranscriptBlock:
    """生成会话内容不可用时的显式占位块。"""
    identifier = str(session_id or "").strip()
    suffix = f" ({identifier})" if identifier else ""
    text = f"Earlier transcript content is unavailable{suffix}."
    block = text_block(text, MUTED_STYLE)

    return TranscriptBlock(
        display_block=block,
        transcript_block=block,
        kind="notice",
        raw_text=text,
    )


def _render_replay_blocks(
    entries: typing.Iterable[TranscriptEntry],
    *,
    terminal_width: int,
    hyperlinks: bool = False,
    terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES
) -> tuple[TranscriptBlock, ...]:
    """把归并后的会话事件转换为 TUI 正文块。"""
    blocks: list[TranscriptBlock] = []

    for entry in entries:
        if entry.event == "message.created":
            block = _message_block(
                entry,
                terminal_width=terminal_width,
                hyperlinks=hyperlinks,
            )
            if block is not None:
                blocks.append(block)
            continue

        if entry.event in {"tool.started", "tool.completed", "tool.failed"}:
            blocks.extend(_tool_blocks(
                entry,
                terminal_width=terminal_width,
                hyperlinks=hyperlinks,
                terminal_capabilities=terminal_capabilities,
            ))
            continue

        if entry.event in {
            "context.compacted",
            "context.compaction.failed",
            "turn.failed",
            "turn.incomplete",
            "turn.interrupted",
        }:
            blocks.append(_notice_block(entry))

    return tuple(blocks)


def _notice_block(entry: TranscriptEntry) -> TranscriptBlock:
    """把压缩和轮次终态转换为简短提示块。"""
    if entry.event.startswith("context."):
        fallback = (
            "Context compacted"
            if entry.event == "context.compacted"
            else "Context compaction failed"
        )
        text = str(entry.payload.get("summary") or fallback).strip()
    else:
        fallback = (
            "Turn interrupted"
            if entry.event == "turn.interrupted"
            else "Turn incomplete"
            if entry.event == "turn.incomplete"
            else "Turn failed"
        )
        text = str(entry.payload.get("error") or fallback).strip()

    block = (
        text_block(text, MUTED_STYLE)
        if entry.event in {"context.compacted", "turn.interrupted"}
        else failure_text_block(text)
    )
    return TranscriptBlock(
        display_block=block,
        transcript_block=block,
        kind="notice",
        source=entry,
        raw_text=text,
    )


def _message_block(
    entry: TranscriptEntry,
    *,
    terminal_width: int,
    hyperlinks: bool = False
) -> TranscriptBlock | None:
    """把用户或助手消息转换为正文块。"""
    content = entry.payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None

    if entry.actor == "user":
        block = query_block(content)
        attachments = entry.payload.get("attachments")
        extras = entry.payload.get("extras")

        return TranscriptBlock(
            display_block=block,
            transcript_block=block,
            kind="user",
            source=entry,
            raw_text=content,
            turn_id=str(entry.turn_id or ""),
            prompt=content,
            attachments=tuple(
                dict(item)
                for item in attachments
                if isinstance(item, dict)
            ) if isinstance(attachments, (list, tuple)) else (),
            extras=dict(extras) if isinstance(extras, dict) else {},
        )

    if entry.actor != "assistant":
        return None

    block = render_tui_assistant_markdown(
        content,
        terminal_width,
        hyperlinks=hyperlinks,
    )

    return TranscriptBlock(
        display_block=block,
        transcript_block=block,
        kind="assistant",
        source=entry,
        raw_text=content,
        source_renderer=partial(
            render_tui_assistant_markdown,
            hyperlinks=hyperlinks,
        ),
        source_render_width=terminal_width,
    )


def _tool_blocks(
    entry: TranscriptEntry,
    *,
    terminal_width: int,
    hyperlinks: bool = False,
    terminal_capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES
) -> tuple[TranscriptBlock, ...]:
    """把工具事件转换为共享展示和完整记录块。"""
    payload = entry.payload
    name = str(payload.get("name") or "tool").strip() or "tool"
    call_id = str(payload.get("call_id") or "").strip()

    if entry.event == "tool.started" and name == "write_stdin":
        return ()

    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    if entry.event == "tool.started":
        preview_data = payload.get("patch_preview")
        if name == "apply_patch":
            if not isinstance(preview_data, dict):
                return ()
            try:
                view = build_tool_start_view(
                    name,
                    arguments,
                    patch_preview=preview_data,
                    call_id=call_id,
                )
            except (KeyError, TypeError, ValueError):
                return ()
        else:
            view = build_tool_start_view(name, arguments, call_id=call_id)
    elif uses_native_tool_view(name):
        result = _transcript_tool_result(
            payload.get("result"),
            expected_ok=_tool_succeeded(entry),
            error=payload.get("error"),
        )
        view = build_native_tool_result_view(
            name,
            arguments,
            ok=result.ok,
            data=result.data,
            cost_ms=_duration_ms(payload),
            call_id=call_id,
        )
    else:
        result = _transcript_tool_result(
            payload.get("result"),
            expected_ok=_tool_succeeded(entry),
            error=payload.get("error"),
        )
        view = build_generic_tool_result_view(
            name,
            result.text,
            ok=result.ok,
            call_id=call_id,
        )

    display = render_presentation_view(
        view,
        terminal_width=terminal_width,
        measure_width=get_cwidth,
        terminal_capabilities=terminal_capabilities,
    )

    transcript = render_presentation_transcript_view(
        view,
        terminal_width=terminal_width,
        measure_width=get_cwidth,
        terminal_capabilities=terminal_capabilities,
    )

    raw = render_presentation_raw_view(view)

    if len(display) != len(transcript) or len(display) != len(raw):
        raise ValueError("replay block projections differ in count")

    return tuple(
        TranscriptBlock(
            display_block=styled_fragment_block(
                display_block,
                hyperlinks=hyperlinks,
            ),
            transcript_block=styled_fragment_block(
                transcript_block,
                hyperlinks=hyperlinks,
            ),
            kind="operation",
            source=entry,
            raw_text=raw_text,
            display_renderer=partial(
                render_presentation_fragment_block,
                view,
                index,
                block_count=len(display),
                hyperlinks=hyperlinks,
                terminal_capabilities=terminal_capabilities,
            ),
            display_render_width=terminal_width,
        )

        for index, (display_block, transcript_block, raw_text) in enumerate(zip(
            display, transcript, raw, strict=True,
        ))
    )


def _duration_ms(payload: dict[str, typing.Any]) -> int | None:
    """读取工具记录中的非负毫秒耗时。"""
    value = payload.get("duration_ms")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


def _transcript_tool_result(
    value: ThawedJsonValue,
    *,
    expected_ok: bool,
    error: ThawedJsonValue,
) -> _TranscriptToolResult:
    """校验并投影当前 transcript 工具结果信封。"""
    if value is None:
        text = error.strip() if isinstance(error, str) else ""
        data: dict[str, ThawedJsonValue] = (
            {"error": text} if text else {}
        )
        return _TranscriptToolResult(
            ok=expected_ok,
            text=text,
            data=data,
        )
    if not isinstance(value, dict):
        raise TypeError("transcript tool result must be an object")

    result_ok = value.get("ok")
    if not isinstance(result_ok, bool):
        raise TypeError("transcript tool result ok must be a boolean")
    if result_ok != expected_ok:
        raise ValueError("transcript tool result ok does not match event status")

    text = value.get("text")
    if not isinstance(text, str):
        raise TypeError("transcript tool result text must be a string")

    attachments = value.get("attachments")
    if not isinstance(attachments, list):
        raise TypeError("transcript tool result attachments must be a list")

    data = value.get("data")
    if not isinstance(data, dict):
        raise TypeError("transcript tool result data must be an object")

    return _TranscriptToolResult(
        ok=result_ok,
        text=text,
        data=dict(data),
    )


def _tool_succeeded(entry: TranscriptEntry) -> bool:
    """读取工具记录中的稳定成功状态。"""
    value = entry.payload.get("ok")
    if isinstance(value, bool):
        return value
    return entry.event == "tool.completed"


if __name__ == '__main__':
    pass
