# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
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
    FAILURE_STYLE,
    MUTED_STYLE,
    command_result_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ..core.runtime import TuiRuntime

_BACK_ACTION      = object()
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
        if not snapshots:
            render_no_agents(mind.frontend.application)
            return None

        selected_id = await runtime.select_menu(agent_list_menu(snapshots))
        if selected_id is None:
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
            if action is _INTERRUPT_ACTION:
                updated = await mind.subagents.interrupt(
                    root_session_id,
                    selected.agent_id,
                )
            elif action is _RESUME_ACTION:
                updated = await mind.subagents.resume(
                    root_session_id,
                    selected.agent_id,
                )
            elif action is _CLOSE_ACTION:
                await mind.subagents.close(
                    root_session_id,
                    selected.agent_id,
                )
                updated = await mind.subagents.get(
                    root_session_id,
                    selected.agent_id,
                )
            else:
                continue
        except (TypeError, ValueError, RuntimeError) as error:
            render_agent_failure(
                mind.frontend.application,
                selected.agent_id,
                error,
            )
            continue

        render_agent_status(mind.frontend.application, updated)


async def append_agent_stream_snapshot(
    runtime: "TuiRuntime",
    mind: "Mind"
) -> None:
    """在流式正文边界排队当前子执行线程快照。"""
    snapshots = await _agent_snapshots(
        mind,
        current_agent_root_session_id(mind),
    )
    runtime.queue_background_block(agent_snapshot_block(snapshots))


def current_agent_root_session_id(mind: typing.Any) -> str:
    """返回当前对话已经建立的根会话标识。"""
    conversation = getattr(mind, "conversation", None)
    return str(getattr(conversation, "sid", "") or "").strip()


def agent_list_menu(
    snapshots: tuple[AgentSnapshot, ...],
) -> MenuRequest:
    """生成当前根会话的子执行线程列表。"""
    active  = sum(snapshot.status in _ACTIVE_STATUSES for snapshot in snapshots)
    queued  = sum(snapshot.queued_count for snapshot in snapshots)
    ordered = _tree_ordered_snapshots(snapshots)

    return MenuRequest(
        title="Agents",
        status=f"active={active} queued={queued} total={len(snapshots)}",
        help_text="Up/Down select · Enter inspect · Esc/q close",
        options=tuple(
            MenuOption(
                value=snapshot.agent_id,
                label=(
                    f"{'  ' * max(0, snapshot.context.depth - 1)}"
                    f"{snapshot.context.agent_type}"
                ),
                detail=(
                    f"{snapshot.status} · {snapshot.agent_id} · "
                    f"turns={snapshot.turn_count} "
                    f"queued={snapshot.queued_count}"
                ),
            )
            for snapshot in ordered
        ),
    )


def agent_detail_menu(snapshot: AgentSnapshot) -> MenuRequest:
    """生成单个子执行线程的详情与可用操作。"""
    actions: list[MenuOption] = [
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

    body = [
        f"ID: {snapshot.agent_id}",
        f"Type: {snapshot.context.agent_type}",
        f"Parent: {snapshot.context.parent_agent_id or '-'}",
        f"Depth: {snapshot.context.depth}",
        f"Session: {snapshot.thread.sid}",
        f"Turns: {snapshot.turn_count}",
        f"Queued: {snapshot.queued_count}",
    ]
    if snapshot.error:
        body.append(f"Error: {_inline_text(snapshot.error)}")
    elif snapshot.result is not None:
        result = getattr(snapshot.result, "assistant_text", "")
        if result:
            body.append(f"Result: {_inline_text(result)}")

    return MenuRequest(
        title="Agent Thread",
        status=snapshot.status,
        body=tuple(body),
        help_text="Up/Down select · Enter apply · Esc/q back",
        options=tuple(actions),
    )


def agent_snapshot_block(
    snapshots: tuple[AgentSnapshot, ...]
) -> FragmentBlock:
    """生成可在正文边界提交的子执行线程快照。"""
    if not snapshots:
        return command_result_block(
            "/agent",
            TextSpan("No agents in this conversation", MUTED_STYLE),
        )

    counts: dict[str, int] = {}
    for snapshot in snapshots:
        counts[snapshot.status] = counts.get(snapshot.status, 0) + 1

    summary = " ".join(
        f"{status}={counts[status]}"
        for status in (
            "pending",
            "running",
            "completed",
            "failed",
            "interrupted",
            "closed",
        )
        if counts.get(status)
    )

    ordered = _tree_ordered_snapshots(snapshots)

    details = "".join(
        (
            f"\n  {snapshot.context.agent_type} · {snapshot.agent_id} · "
            f"{snapshot.status} · turns={snapshot.turn_count} "
            f"queued={snapshot.queued_count}"
        )
        for snapshot in ordered[:4]
    )

    omitted = len(snapshots) - 4
    if omitted > 0:
        details += f"\n  … {omitted} more"

    return command_result_block(
        "/agent",
        TextSpan(summary, BRIGHT_STYLE),
        TextSpan(details, MUTED_STYLE),
    )


def render_no_agents(application: ApplicationSink) -> None:
    """展示当前对话没有子执行线程。"""
    application.emit(ApplicationView(
        type="tui.agents.status",
        renderable=agent_snapshot_block(()),
    ))
    application.emit(ApplicationView(type="tui.gap"))


def render_agent_status(
    application: ApplicationSink,
    snapshot: AgentSnapshot
) -> None:
    """展示子执行线程操作后的状态。"""
    application.emit(ApplicationView(
        type="tui.agents.status",
        renderable=command_result_block(
            "/agent",
            TextSpan(snapshot.context.agent_type, BRIGHT_STYLE),
            TextSpan(
                f" · {snapshot.agent_id} · {snapshot.status}",
                MUTED_STYLE,
            ),
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))


def render_agent_failure(
    application: ApplicationSink,
    agent_id: str,
    error: BaseException
) -> None:
    """展示子执行线程管理操作失败。"""
    message = str(error).strip() or type(error).__name__

    application.emit(ApplicationView(
        type="tui.agents.failure",
        renderable=command_result_block(
            "/agent",
            TextSpan("Failed", FAILURE_STYLE),
            TextSpan(f" · {agent_id} · {_inline_text(message)}", MUTED_STYLE),
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))


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
