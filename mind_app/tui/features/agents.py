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
    CLOSE_MENU_FOOTER_HINT,
    FragmentBlock,
    MenuActionKind,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..core.styles import (
    BRIGHT_STYLE,
    COMMAND_STYLE,
    FAILURE_STYLE,
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
    snapshots       = await _agent_snapshots(mind, root_session_id)

    def root_menu(items: tuple[AgentSnapshot, ...]) -> MenuRequest:
        return agent_list_menu(
            items,
            root_session_id=root_session_id,
            on_agent_selected=open_agent_menu,
        )

    def open_agent_menu(snapshot: AgentSnapshot) -> None:

        def run_agent_action(action: typing.Any) -> None:
            """启动子执行线程菜单后台操作。"""
            runtime.start_background_task(
                _run_agent_action(
                    runtime,
                    mind,
                    root_session_id,
                    snapshot,
                    action,
                    root_menu,
                ),
                name="tui agent menu action",
            )

        def start_agent_action(action: typing.Any) -> None:
            """启动子执行线程菜单操作。"""
            runtime.emit_menu_action(
                lambda: run_agent_action(action),
                name="tui agent menu action",
                kind=MenuActionKind.DOMAIN,
            )

        def push_agent_menu() -> None:
            """压入子执行线程操作菜单。"""
            runtime.push_menu(agent_detail_menu(
                snapshot,
                root_session_id=root_session_id,
                on_action=start_agent_action,
            ))

        runtime.emit_menu_action(
            push_agent_menu,
            name="tui agent navigation",
            kind=MenuActionKind.NAVIGATION,
        )

    await runtime.select_menu(root_menu(snapshots))


async def _run_agent_action(
    runtime: "TuiRuntime",
    mind: "Mind",
    root_session_id: str,
    snapshot: AgentSnapshot,
    action: typing.Any,
    root_menu: typing.Callable[[tuple[AgentSnapshot, ...]], MenuRequest]
) -> None:
    """执行子执行线程菜单动作并刷新仍然可见的父级菜单。"""
    session_id = runtime.active_menu_session_id()
    if session_id is None:
        return None

    try:
        if action is _SHOW_ACTION:
            current = await mind.subagents.get(
                root_session_id,
                snapshot.agent_id,
            )
            if not runtime.menu_session_is_active(session_id):
                return None
            runtime.append_block(
                agent_snapshot_block(current),
                kind="notice",
            )
            runtime.finish_menu(None)
            return None
        if action is _INTERRUPT_ACTION:
            await mind.subagents.interrupt(root_session_id, snapshot.agent_id)
        elif action is _RESUME_ACTION:
            await mind.subagents.resume(root_session_id, snapshot.agent_id)
        elif action is _CLOSE_ACTION:
            await mind.subagents.close(root_session_id, snapshot.agent_id)
        else:
            return None

        refreshed = await _agent_snapshots(mind, root_session_id)
        if runtime.menu_session_is_active(session_id):
            runtime.replace_active_menu_if_id(
                _agent_root_view_id(root_session_id),
                root_menu(refreshed),
                session_id=session_id,
            )
    except (TypeError, ValueError, RuntimeError) as error:
        if runtime.menu_session_is_active(session_id):
            runtime.push_menu(agent_failure_panel(snapshot, error))


async def _agent_snapshots(mind: "Mind", root_session_id: str) -> tuple[AgentSnapshot, ...]:
    """读取当前根会话快照，不存在运行树时返回空集合。"""
    if not root_session_id:
        return ()

    try:
        return await mind.subagents.snapshots(root_session_id)
    except AgentNotFoundError:
        return ()


def current_agent_root_session_id(mind: typing.Any) -> str:
    """返回当前对话已经建立的根会话标识。"""
    conversation = getattr(mind, "conversation", None)
    return str(getattr(conversation, "sid", "") or "").strip()


def agent_list_menu(
    snapshots: tuple[AgentSnapshot, ...],
    *,
    root_session_id: str = "",
    on_agent_selected: typing.Callable[[AgentSnapshot], None] | None = None
) -> MenuRequest:
    """生成当前根会话的子执行线程列表。"""
    active  = sum(snapshot.status in _ACTIVE_STATUSES for snapshot in snapshots)
    queued  = sum(snapshot.queued_count for snapshot in snapshots)
    ordered = _tree_ordered_snapshots(snapshots)

    options = [MenuOption(
        value=_MAIN_ACTION,
        label="• Main [default]",
        detail=root_session_id or "current session",
        is_current=True,
        is_default=True,
    )]
    for snapshot in ordered:
        options.append(MenuOption(
            value=snapshot.agent_id,
            label=(
                f"{'  ' * max(0, snapshot.context.depth - 1)}"
                f"• {snapshot.context.task_path}"
            ),
            detail=f"{snapshot.agent_id} · {snapshot.status}",
            on_select=(
                lambda selected=snapshot: on_agent_selected(selected)
                if on_agent_selected is not None
                else None
            ),
            dismiss_on_select=on_agent_selected is None,
        ))

    return MenuRequest(
        title="Sub-agents",
        view_id=_agent_root_view_id(root_session_id),
        status=f"active={active} queued={queued} total={len(snapshots)}",
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(options),
    )


def agent_snapshot_block(snapshot: AgentSnapshot) -> FragmentBlock:
    """生成单个执行线程写入正文的当前状态快照。"""
    status_parts: list[str | TextSpan] = []
    if snapshot.status == "failed":
        status_parts.append(TextSpan("■ ", FAILURE_STYLE))
    status_parts.append(TextSpan(
        f"{snapshot.status} · turns={snapshot.turn_count} "
        f"queued={snapshot.queued_count}",
        MUTED_STYLE,
    ))

    parts: list[str | TextSpan] = [
        TextSpan("/agent ", COMMAND_STYLE),
        TextSpan("· ", MUTED_STYLE),
        TextSpan(snapshot.context.task_path, BRIGHT_STYLE),
        "\n",
        *status_parts,
    ]

    for line in _agent_activity(snapshot):
        parts.append("\n")
        parts.append(TextSpan(f"  {line}", MUTED_STYLE))

    return fragment_block(*parts)


def agent_detail_menu(
    snapshot: AgentSnapshot,
    *,
    root_session_id: str = "",
    on_action: typing.Callable[[typing.Any], None] | None = None
) -> MenuRequest:
    """生成单个子执行线程的可用操作。"""
    actions: list[MenuOption] = [
        MenuOption(
            value=_SHOW_ACTION,
            label="Show",
            detail="write the current snapshot to the transcript",
            on_select=(
                lambda: on_action(_SHOW_ACTION)
                if on_action is not None
                else None
            ),
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
            on_select=(
                lambda: on_action(_INTERRUPT_ACTION)
                if on_action is not None
                else None
            ),
        ))
    if snapshot.status == "closed":
        actions.append(MenuOption(
            value=_RESUME_ACTION,
            label="Resume",
            detail="reopen this thread for future input",
            on_select=(
                lambda: on_action(_RESUME_ACTION)
                if on_action is not None
                else None
            ),
        ))
    else:
        actions.append(MenuOption(
            value=_CLOSE_ACTION,
            label="Close",
            detail="close this thread and its descendants",
            on_select=(
                lambda: on_action(_CLOSE_ACTION)
                if on_action is not None
                else None
            ),
        ))

    return MenuRequest(
        title="Agent actions",
        view_id=_agent_detail_view_id(root_session_id, snapshot.agent_id),
        status=(
            f"{snapshot.context.task_path} · "
            f"{snapshot.status}"
        ),
        help_text="",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
        options=tuple(actions),
    )


def agent_failure_panel(
    snapshot: AgentSnapshot,
    error: BaseException
) -> MenuRequest:
    """生成子执行线程管理失败面板。"""
    message = str(error).strip() or type(error).__name__
    return MenuRequest(
        title="Agent operation",
        view_id=f"agents:failure:{snapshot.agent_id}",
        status=snapshot.context.task_path,
        body=(f"■ Failed: {_inline_text(message)}",),
        help_text="",
        footer_hint=CLOSE_MENU_FOOTER_HINT,
    )


def _agent_root_view_id(root_session_id: str) -> str:
    """生成子执行线程列表的稳定菜单标识。"""
    return f"agents:list:{root_session_id or 'current'}"


def _agent_detail_view_id(root_session_id: str, agent_id: str) -> str:
    """生成子执行线程操作菜单的稳定标识。"""
    return f"agents:detail:{root_session_id or 'current'}:{agent_id}"


def _tree_ordered_snapshots(
    snapshots: tuple[AgentSnapshot, ...]
) -> tuple[AgentSnapshot, ...]:
    """按创建稳定性把扁平快照投影为父子先序列表。"""
    children: dict[str, list[AgentSnapshot]] = {}
    known_ids = {snapshot.agent_id for snapshot in snapshots}
    roots: list[AgentSnapshot] = []

    for snapshot in snapshots:
        parent_id_value = snapshot.context.parent_agent_id
        if not isinstance(parent_id_value, str) or parent_id_value not in known_ids:
            roots.append(snapshot)
            continue
        parent_id: str = parent_id_value
        children.setdefault(parent_id, []).append(snapshot)

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
