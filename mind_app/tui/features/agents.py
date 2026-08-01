# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from mind_app.history.transcript import (
    TranscriptEntry,
    TranscriptReader,
    TranscriptReplay
)
from mind_app.presentation.models import TextSpan
from mind_app.presentation.terminal_text import sanitize_terminal_text
from mind_app.runtime.subagents.control import (
    AgentNotFoundError,
    AgentSnapshot
)
from ..core.models import (
    FragmentBlock,
    MenuOption,
    MenuRequest
)
from ..core.styles import (
    BRIGHT_STYLE,
    COMMAND_STYLE,
    MUTED_STYLE,
    fragment_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ..core.runtime import TuiRuntime

_MAIN_ACTION      = object()
_BACK_ACTION      = object()
_SHOW_ACTION      = object()
_INTERRUPT_ACTION = object()
_RESUME_ACTION    = object()
_CLOSE_ACTION     = object()

_ACTIVE_STATUSES = frozenset({"pending", "running"})


async def manage_agents(
    runtime: "TuiRuntime",
    mind: "Mind"
) -> None:
    """在主 TUI 中查看并管理当前根会话的子执行线程。"""
    root_session_id = current_agent_root_session_id(mind)

    while True:
        snapshots = await _agent_snapshots(mind, root_session_id)

        selected_id = await runtime.select_menu(agent_list_menu(
            snapshots,
            root_session_id=root_session_id,
        ))
        if selected_id is None or selected_id is _MAIN_ACTION:
            return None

        selected = next(
            (
                snapshot
                for snapshot in snapshots
                if snapshot.agent_id == selected_id
            ),
            None,
        )
        if selected is None:
            continue

        action = await runtime.select_menu(agent_detail_menu(selected))
        if action is None or action is _BACK_ACTION:
            continue

        try:
            if action is _SHOW_ACTION:
                current = await mind.subagents.get(
                    root_session_id,
                    selected.agent_id,
                )
                runtime.append_block(
                    agent_snapshot_block(current),
                    kind="notice",
                )
                return None
            elif action is _INTERRUPT_ACTION:
                await mind.subagents.interrupt(
                    root_session_id,
                    selected.agent_id,
                )
            elif action is _RESUME_ACTION:
                await mind.subagents.resume(
                    root_session_id,
                    selected.agent_id,
                )
            elif action is _CLOSE_ACTION:
                await mind.subagents.close(
                    root_session_id,
                    selected.agent_id,
                )
            else:
                continue
        except (TypeError, ValueError, RuntimeError) as error:
            await runtime.select_menu(agent_failure_panel(selected, error))
            continue


def current_agent_root_session_id(mind: typing.Any) -> str:
    """返回当前对话已经建立的根会话标识。"""
    conversation = getattr(mind, "conversation", None)
    return str(getattr(conversation, "sid", "") or "").strip()


def agent_list_menu(
    snapshots: tuple[AgentSnapshot, ...],
    *,
    root_session_id: str = ""
) -> MenuRequest:
    """生成当前根会话的子执行线程列表。"""
    active  = sum(snapshot.status in _ACTIVE_STATUSES for snapshot in snapshots)
    queued  = sum(snapshot.queued_count for snapshot in snapshots)
    ordered = _tree_ordered_snapshots(snapshots)

    options = [MenuOption(
        value=_MAIN_ACTION,
        label="• Main [default] (current)",
        detail=root_session_id or "current session",
    )]
    options.extend(
        MenuOption(
            value=snapshot.agent_id,
            label=(
                f"{'  ' * max(0, snapshot.context.depth - 1)}"
                f"• {snapshot.context.task_path}"
            ),
            detail=f"{snapshot.agent_id} · {snapshot.status}",
        )
        for snapshot in ordered
    )

    return MenuRequest(
        title="Sub-agents",
        status=f"active={active} queued={queued} total={len(snapshots)}",
        body=("No sub-agents.",) if not snapshots else (),
        help_text="Up/Down select · Enter manage · Esc/q close",
        options=tuple(options),
    )


def agent_snapshot_block(snapshot: AgentSnapshot) -> FragmentBlock:
    """生成单个执行线程写入正文的当前状态快照。"""
    parts: list[str | TextSpan] = [
        TextSpan("/agent ", COMMAND_STYLE),
        TextSpan("· ", MUTED_STYLE),
        TextSpan(snapshot.context.task_path, BRIGHT_STYLE),
        "\n",
        TextSpan(
            f"{snapshot.status} · turns={snapshot.turn_count} "
            f"queued={snapshot.queued_count}",
            MUTED_STYLE,
        ),
    ]

    for line in _agent_activity(snapshot):
        parts.append("\n")
        parts.append(TextSpan(f"  {line}", MUTED_STYLE))

    return fragment_block(*parts)


def agent_detail_menu(snapshot: AgentSnapshot) -> MenuRequest:
    """生成单个子执行线程的可用操作。"""
    actions: list[MenuOption] = [
        MenuOption(
            value=_SHOW_ACTION,
            label="Show",
            detail="write the current snapshot to the transcript",
        ),
        MenuOption(
            value=_BACK_ACTION,
            label="Back",
            detail="return to agent list",
        ),
    ]
    if snapshot.status in _ACTIVE_STATUSES:
        actions.append(MenuOption(
            value=_INTERRUPT_ACTION,
            label="Interrupt",
            detail="stop the current turn and keep the thread open",
        ))
    if snapshot.status == "closed":
        actions.append(MenuOption(
            value=_RESUME_ACTION,
            label="Resume",
            detail="reopen this thread for future input",
        ))
    else:
        actions.append(MenuOption(
            value=_CLOSE_ACTION,
            label="Close",
            detail="close this thread and its descendants",
        ))

    return MenuRequest(
        title="Agent actions",
        status=f"{snapshot.context.task_path} · {snapshot.status}",
        help_text="Up/Down select · Enter apply · Esc/q back",
        options=tuple(actions),
    )


def agent_failure_panel(
    snapshot: AgentSnapshot,
    error: BaseException
) -> MenuRequest:
    """生成子执行线程管理失败面板。"""
    message = str(error).strip() or type(error).__name__
    return MenuRequest(
        title="Agent operation failed",
        status=snapshot.context.task_path,
        body=(_inline_text(message),),
        help_text="Enter/Esc/q close",
    )


async def _agent_snapshots(
    mind: "Mind",
    root_session_id: str
) -> tuple[AgentSnapshot, ...]:
    """读取当前根会话快照，不存在运行树时返回空集合。"""
    if not root_session_id:
        return ()

    try:
        return await mind.subagents.snapshots(root_session_id)
    except AgentNotFoundError:
        return ()


def _tree_ordered_snapshots(
    snapshots: tuple[AgentSnapshot, ...]
) -> tuple[AgentSnapshot, ...]:
    """按创建稳定性把扁平快照投影为父子先序列表。"""
    children: dict[str, list[AgentSnapshot]] = {}
    known_ids = {snapshot.agent_id for snapshot in snapshots}
    roots: list[AgentSnapshot] = []

    for snapshot in snapshots:
        parent_id = snapshot.context.parent_agent_id
        if parent_id in known_ids:
            children.setdefault(str(parent_id), []).append(snapshot)
        else:
            roots.append(snapshot)

    ordered: list[AgentSnapshot] = []

    def append_subtree(snapshot: AgentSnapshot) -> None:
        """按原始创建顺序追加一个线程及其后代。"""
        ordered.append(snapshot)
        descendants = children.get(snapshot.agent_id)
        if descendants is None:
            return None
        for child in descendants:
            append_subtree(child)

    for root in roots:
        append_subtree(root)
    return tuple(ordered)


def _agent_activity(snapshot: AgentSnapshot) -> tuple[str, ...]:
    """读取子执行线程最近的有界消息和工具活动。"""
    path = str(snapshot.thread.transcript_path or "").strip()
    if path:
        entries = TranscriptReplay(
            TranscriptReader(path).read_tail(40)
        ).build()
        activity = tuple(
            text
            for entry in reversed(entries)
            for text in [_activity_text(entry)]
            if text
        )[:2]
        if activity:
            return tuple(reversed(activity))

    if snapshot.status == "pending":
        return ("Waiting to start",)
    return (
        f"turns={snapshot.turn_count} queued={snapshot.queued_count}",
    )


def _activity_text(entry: TranscriptEntry) -> str:
    """把一项持久事件转换为单行活动摘要。"""
    if entry.event in {"tool.started", "tool.completed", "tool.failed"}:
        name = str(entry.payload.get("name") or "tool").strip() or "tool"
        arguments = entry.payload.get("arguments")
        if name == "shell_command" and isinstance(arguments, dict):
            command = _inline_text(arguments.get("command"), limit=160)
            return f"$ {command}" if command else "$ shell_command"
        if isinstance(arguments, dict) and arguments:
            serialized = json.dumps(
                arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
            return _inline_text(f"$ {name} {serialized}", limit=160)
        return f"$ {name}"

    if entry.event == "message.created" and entry.actor == "assistant":
        return _inline_text(entry.payload.get("content"), limit=160)

    return ""


def _inline_text(value: typing.Any, limit: int = 240) -> str:
    """返回适合菜单正文的单行有界文本。"""
    text       = sanitize_terminal_text(str(value or ""))
    normalized = " ".join(text.split())

    return (
        normalized
        if len(normalized) <= limit
        else f"{normalized[:limit]}..."
    )


if __name__ == '__main__':
    pass
