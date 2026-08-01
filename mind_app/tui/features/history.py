# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
from prompt_toolkit.utils import get_cwidth
from mind_app.history.transcript import (
    TranscriptEntry,
    TranscriptReplay
)
from mind_app.presentation.renderers.dispatch import (
    render_presentation_transcript_view,
    render_presentation_view
)
from mind_app.presentation.tool_views import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view
)
from mind_app.stream_events.tool_trace import coding_trace_tool
from ..adapters.markdown import render_tui_markdown
from ..core.document import TranscriptBlock
from ..core.models import (
    FragmentBlock,
    MenuOption,
    MenuRequest
)
from ..core.styles import (
    FAILURE_STYLE,
    MUTED_STYLE,
    assistant_block,
    query_block,
    styled_block_fragments,
    text_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ..core.runtime import TuiRuntime


async def choose_history_session(
    runtime: "TuiRuntime",
    records: list[dict[str, typing.Any]],
    *,
    show_workspace: bool = False
) -> dict[str, typing.Any] | None:
    """在主 TUI 中选择一项历史会话。"""
    if not records:
        return None

    return await runtime.select_menu(MenuRequest(
        title="Resume conversation",
        status=f"items={len(records)}",
        help_text="Up/Down select · PgUp/PgDn jump · Enter resume · Esc/q cancel",
        options=tuple(
            MenuOption(
                value=record,
                label=_record_prefix(record),
                detail=_record_detail(
                    record,
                    show_workspace=show_workspace,
                ),
            )
            for record in records
        ),
    ))


def load_history_transcript(
    controller: "Mind",
    session_id: str,
    *,
    terminal_width: int
) -> tuple[TranscriptBlock, ...]:
    """读取会话事件并生成可一次性提交的恢复块。"""
    entries = (
        entry
        for entry in controller.read_conversation_transcript(session_id)
        if entry.session_id == session_id
    )

    replay = TranscriptReplay(entries).build()
    return _render_replay_blocks(replay, terminal_width=terminal_width)


def _render_replay_blocks(
    entries: typing.Iterable[TranscriptEntry],
    *,
    terminal_width: int
) -> tuple[TranscriptBlock, ...]:
    """把归并后的会话事件转换为 TUI 正文块。"""
    blocks: list[TranscriptBlock] = []

    for entry in entries:
        if entry.event == "message.created":
            block = _message_block(entry)
            if block is not None:
                blocks.append(block)
            continue

        if entry.event in {"tool.started", "tool.completed", "tool.failed"}:
            blocks.extend(_tool_blocks(
                entry,
                terminal_width=terminal_width,
            ))
            continue

        if entry.event in {
            "context.compacted",
            "context.compaction.failed",
            "turn.failed",
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
            else "Turn failed"
        )
        text = str(entry.payload.get("error") or fallback).strip()

    style = (
        MUTED_STYLE
        if entry.event == "context.compacted"
        else FAILURE_STYLE
    )
    block = text_block(text, style)
    return TranscriptBlock(
        display_block=block,
        transcript_block=block,
        kind="notice",
    )


def _message_block(entry: TranscriptEntry) -> TranscriptBlock | None:
    """把用户或助手消息转换为正文块。"""
    content = entry.payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None

    if entry.actor == "user":
        block       = query_block(content)
        attachments = entry.payload.get("attachments")
        extras      = entry.payload.get("extras")

        return TranscriptBlock(
            display_block=block,
            transcript_block=block,
            kind="user",
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

    try:
        block = assistant_block(render_tui_markdown(content))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        block = assistant_block(text_block(content))

    return TranscriptBlock(
        display_block=block,
        transcript_block=block,
        kind="assistant",
    )


def _tool_blocks(
    entry: TranscriptEntry,
    *,
    terminal_width: int
) -> tuple[TranscriptBlock, ...]:
    """把工具事件转换为共享展示和完整记录块。"""
    payload = entry.payload
    name    = str(payload.get("name") or "tool").strip() or "tool"
    call_id = str(payload.get("call_id") or "").strip()

    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    if entry.event == "tool.started":
        view = build_tool_start_view(name, arguments, call_id=call_id)
    elif coding_trace_tool(name):
        duration = payload.get("duration_ms")
        cost_ms = duration if isinstance(duration, int) and not isinstance(
            duration,
            bool,
        ) else None
        view = build_native_tool_result_view(
            name,
            arguments,
            ok=entry.event == "tool.completed",
            data=payload.get("result"),
            cost_ms=cost_ms,
            call_id=call_id,
        )
    else:
        result = payload.get("result")
        if result is None:
            result = payload.get("error")

        view = build_generic_tool_result_view(
            name,
            _stable_text(result),
            ok=entry.event == "tool.completed",
            call_id=call_id,
        )

    display = render_presentation_view(
        view,
        terminal_width=terminal_width,
        measure_width=get_cwidth,
    )

    transcript = render_presentation_transcript_view(
        view,
        terminal_width=terminal_width,
        measure_width=get_cwidth,
    )
    if len(display) != len(transcript):
        raise ValueError("replay display and transcript block counts differ")

    return tuple(
        TranscriptBlock(
            display_block=FragmentBlock(styled_block_fragments(display_block)),
            transcript_block=FragmentBlock(styled_block_fragments(
                transcript_block
            )),
            kind="operation",
        )
        for display_block, transcript_block in zip(display, transcript)
    )


def _stable_text(value: typing.Any) -> str:
    """把工具结果转换为稳定文本。"""
    if isinstance(value, str):
        return value
    if value is None:
        return ""

    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    )


def _record_detail(
    record: dict[str, typing.Any],
    *,
    show_workspace: bool
) -> str:
    """返回包含可选工作区的会话说明。"""
    title = _record_title(record)
    if not show_workspace:
        return title

    workspace = str(record.get("workspace") or "-").strip() or "-"
    return f"{title} · {workspace}"


def _record_title(record: dict[str, typing.Any]) -> str:
    """返回历史会话的展示标题。"""
    title = str(record.get("title") or "").strip()
    if not title:
        title = str(record.get("cid") or "-").strip()
    return title


def _record_prefix(record: dict[str, typing.Any]) -> str:
    """返回历史会话的更新时间。"""
    return _format_updated_at(record.get("updated_at"))


def _format_updated_at(value: typing.Any) -> str:
    """格式化毫秒时间戳。"""
    try:
        timestamp = int(value) / 1000
    except (TypeError, ValueError):
        return "-"
    return time.strftime("%m-%d %H:%M", time.localtime(timestamp))


if __name__ == '__main__':
    pass
