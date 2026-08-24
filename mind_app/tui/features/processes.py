# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import shutil
import typing
import asyncio
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.utils import get_cwidth
from mind_app.presentation.models import TextSpan
from mind_app.presentation.terminal_text import sanitize_terminal_text
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from ..core.models import FragmentBlock
from ..core.process_viewer import ProcessViewerRequest
from ..rendering.fragments import (
    clip_fragments,
    clip_text
)
from .context import exec_status_display_label
from ..core.status_frames import status_indicator_fragment
from ..core.styles import (
    BODY_STYLE,
    BRIGHT_STYLE,
    fragment_block,
)
from .summary import (
    CommandSummary,
    command_summary_text,
    command_summary_title_parts,
)

if typing.TYPE_CHECKING:
    from ..runtime.ports import ProcessRuntimePort

PS_EVENT_WAIT_TIMEOUT_SEC: float = 1.0
PS_INTERRUPT_GRACE_SEC: float    = 0.05
PS_OUTPUT_LIMIT: int             = 120000
PS_VISIBLE_OUTPUT_LINES: int     = 8
SHELL_VISIBLE_OUTPUT_LINES: int  = 50
PS_STREAM_VISIBLE_PROCESSES: int = 3
PS_HISTORY_VISIBLE_PROCESSES: int = 16
PS_STREAM_OUTPUT_LINES: int      = 3
PROCESS_STATUS_EVENT_WAIT_SEC: float = 3600.0
SHELL_ACTIVITY_FRAME_SEC: float = 0.08

ProcessViewerMode: typing.TypeAlias = typing.Literal[
    "process",
    "inline",
]

ExecSnapshotMode: typing.TypeAlias = typing.Literal[
    "stream",
    "history",
]

PROCESS_VIEWER_FOCUS_REQUEST = ProcessViewerRequest(
    fragments=(("", " \n "),),
    max_height=2,
)

async def monitor_exec_status(
    runtime: "ProcessRuntimePort",
    mind: typing.Any
) -> None:
    """同步后台命令会话摘要到 TUI 专属状态行。"""
    revision = -1
    try:
        while not mind.task_event.is_set():
            try:
                snapshot = await mind.native_coding.running_exec_sessions()
                filtered = _without_running_session(
                    snapshot,
                    runtime.inline_process_session_id,
                    runtime=runtime,
                )
                _set_exec_status(runtime, filtered)
                revision = int(snapshot.get("revision") or revision)
            except (OSError, RuntimeError, TypeError, ValueError):
                revision = -1

            change_task = asyncio.create_task(
                mind.native_coding.wait_exec_sessions_update(
                    revision=revision,
                    timeout_sec=PROCESS_STATUS_EVENT_WAIT_SEC,
                ),
                name="process status event wait",
            )
            stop_task = asyncio.create_task(
                mind.task_event.wait(),
                name="process status stop wait",
            )
            wait_tasks = {change_task, stop_task}
            try:
                done, _pending = await asyncio.wait(
                    wait_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for task in wait_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*wait_tasks, return_exceptions=True)

            if stop_task in done:
                break

            change = change_task.result()
            if isinstance(change, dict) and change.get("changed"):
                revision = int(change.get("revision") or revision)

    finally:
        runtime.set_process_status_label("")


def _set_exec_status(
    runtime: "ProcessRuntimePort",
    snapshot: typing.Any,
    *,
    excluded_session_id: str = ""
) -> None:
    """更新排除当前前台会话后的后台进程摘要。"""
    filtered = _without_running_session(
        snapshot,
        excluded_session_id,
        runtime=runtime,
    )

    runtime.set_process_status_label(exec_status_display_label(
        filtered,
        line_width=runtime.terminal_width,
    ))


def _without_running_session(
    snapshot: typing.Any,
    session_id: str,
    *,
    runtime: "ProcessRuntimePort",
) -> dict[str, typing.Any]:
    """返回按来源和当前会话过滤后的后台进程快照。"""
    current = dict(snapshot) if isinstance(snapshot, dict) else {}
    excluded = str(session_id or "").strip()
    background_ids = frozenset(runtime.background_process_session_ids)
    inline_ids = frozenset(runtime.inline_process_session_ids)
    items = [
        item
        for item in _running_items(current)
        if not excluded
        or str(item.get("session_id") or "").strip() != excluded
        if _is_background_session_item(
            item,
            inline_ids=inline_ids,
            background_ids=background_ids,
        )
    ]
    current["items"] = items
    current["count"] = len(items)
    current["background_items"] = items
    current["background_count"] = len(items)
    return current


def _is_background_session_item(
    item: dict[str, typing.Any],
    *,
    inline_ids: frozenset[str],
    background_ids: frozenset[str],
) -> bool:
    """判断单个会话是否属于当前 TUI 的后台终端投影。"""
    if item.get("background") is True:
        return True

    if str(item.get("origin") or "") != "tui_shell":
        return True

    session_id = str(item.get("session_id") or "").strip()
    if session_id in background_ids:
        return True
    if session_id in inline_ids:
        return False
    # 未出现在当前前台集合中的 tui_shell 会话属于后台投影。
    return True


async def manage_exec_sessions(
    runtime: "ProcessRuntimePort",
    mind: typing.Any
) -> bool:
    """在主历史中追加一次后台命令快照，不打开详情面板。"""
    await append_exec_history_snapshot(runtime, mind)
    return True


async def stop_all_exec_sessions(
    runtime: "ProcessRuntimePort",
    mind: typing.Any,
    *,
    sessions: list[dict[str, typing.Any]] | None = None
) -> None:
    """停止当前全部后台终端会话。"""
    application = mind.frontend.application
    application.emit(ApplicationView(
        type="tui.exec.stopping",
        renderable=fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan("Stopping all background terminals.", BRIGHT_STYLE),
        ),
    ))

    if sessions is None:
        snapshot = await mind.native_coding.running_exec_sessions()
        sessions = _without_running_session(
            snapshot,
            runtime.inline_process_session_id,
            runtime=runtime,
        )["items"]

    if not sessions:
        return None

    session_ids = tuple(
        str(item.get("session_id") or "").strip()
        for item in sessions
        if str(item.get("session_id") or "").strip()
    )
    result = await mind.native_coding.stop_exec_sessions(
        session_ids=session_ids,
    )

    for item in _result_items(result, "items"):
        runtime.cancel_background_session_task(item.get("session_id"))

    runtime.set_process_status_label("")


def render_no_background_terminals(
    application: ApplicationSink,
    *,
    command: str | None = None
) -> None:
    """渲染当前没有后台终端的状态。"""
    block = _no_background_terminals_block(command=command)

    application.emit(ApplicationView(
        type="tui.exec.empty",
        renderable=block,
    ))


async def append_exec_stream_snapshot(
    runtime: "ProcessRuntimePort",
    mind: typing.Any
) -> None:
    """在模型流式期间追加后台终端的近期输出摘要。"""
    await _append_exec_snapshot(runtime, mind, mode="stream")


async def append_exec_history_snapshot(
    runtime: "ProcessRuntimePort",
    mind: typing.Any
) -> None:
    """在主历史中追加一次稳定的后台终端快照。"""
    await _append_exec_snapshot(runtime, mind, mode="history")


async def _append_exec_snapshot(
    runtime: "ProcessRuntimePort",
    mind: typing.Any,
    *,
    mode: ExecSnapshotMode,
) -> None:
    """读取后台会话并按指定表面提交一次摘要。"""
    try:
        listing  = await mind.native_coding.running_exec_sessions()
        excluded_session_id = runtime.inline_process_session_id
        sessions = _without_running_session(
            listing,
            excluded_session_id,
            runtime=runtime,
        )["items"]

        if not sessions:
            block = _no_background_terminals_block(command="/ps")
        else:
            visible_limit = (
                PS_HISTORY_VISIBLE_PROCESSES
                if mode == "history"
                else PS_STREAM_VISIBLE_PROCESSES
            )
            visible_sessions = sessions[:visible_limit]
            snapshots = await asyncio.gather(*(
                _load_exec_stream_snapshot(mind, session)
                for session in visible_sessions
            ))
            block = exec_stream_snapshots_block(
                snapshots,
                omitted_count=max(0, len(sessions) - len(visible_sessions)),
                terminal_width=runtime.terminal_width,
                mode=mode,
            )

    except Exception as exc:
        block = _exec_stream_snapshot_error_block(
            exc,
            terminal_width=runtime.terminal_width,
            mode=mode,
        )

    runtime.append_block(block, kind="operation")


async def _load_exec_stream_snapshot(
    mind: typing.Any,
    session: dict[str, typing.Any]
) -> dict[str, typing.Any]:
    """读取单个后台终端快照并保留列表中的摘要字段。"""
    session_id = str(session.get("session_id") or "").strip()

    try:
        snapshot = await mind.native_coding.exec_session_output_snapshot(
            session_id=session_id,
            max_output_chars=PS_OUTPUT_LIMIT,
        )
    except Exception as exc:
        return {
            **session,
            "ok": False,
            "reason": _inline_text(exc) or type(exc).__name__,
        }

    if not isinstance(snapshot, dict):
        return {
            **session,
            "ok": False,
            "reason": "snapshot unavailable",
        }
    return {**session, **snapshot}


def exec_stream_snapshots_block(
    snapshots: typing.Sequence[dict[str, typing.Any]],
    *,
    omitted_count: int = 0,
    terminal_width: int | None = None,
    mode: ExecSnapshotMode = "stream",
) -> FragmentBlock:
    """生成流式或历史状态使用的后台终端摘要。"""
    width = _terminal_width(terminal_width)

    if mode == "history":
        fragments: list[tuple[str, str]] = [
            ("class:prompt.command.slash", "/ps"),
            ("", "\n\n"),
            ("class:ps.title", "Background terminals"),
            ("", "\n\n"),
        ]
    else:
        fragments = [
            ("class:prompt.command.slash", "/ps"),
            ("class:ps.meta", " · "),
            ("class:ps.title", "Background terminals"),
            ("", "\n\n"),
        ]

    rows: list[StyleAndTextTuples] = []

    for snapshot in snapshots:
        command = _clip_inline(
            snapshot.get("command") or "(unknown command)",
            width - 4,
        )
        rows.append([
            ("class:ps.stream", "  • "),
            ("class:ps.stream.command", command),
        ])

        if snapshot.get("ok") is False:
            output_lines = [
                _inline_text(snapshot.get("reason")) or "snapshot failed"
            ]
        else:
            output_lines = _panel_output_lines(
                snapshot,
                limit=PS_STREAM_OUTPUT_LINES,
            ) or ["(waiting for output)"]

        for index, line in enumerate(output_lines):
            prefix = "    ↳ " if index == 0 else "      "
            rows.append([(
                "class:ps.stream",
                f"{prefix}{_clip_inline(line, width - get_cwidth(prefix))}",
            )])

    if omitted_count > 0:
        rows.append([(
            "class:ps.stream",
            f"  … and {omitted_count} more running",
        )])

    for index, row in enumerate(rows):
        fragments.extend(row)
        if index < len(rows) - 1:
            fragments.append(("", "\n"))

    return FragmentBlock(tuple(fragments))


def _no_background_terminals_block(
    *,
    command: str | None
) -> FragmentBlock:
    """生成当前没有后台终端的状态块。"""
    fragments: list[tuple[str, str]] = []

    if command:
        fragments.extend([
            ("class:prompt.command.slash", command),
            ("", "\n\n"),
            ("class:ps.title", "Background terminals"),
            ("", "\n\n"),
        ])
    else:
        fragments.extend([
            ("class:ps.title", "Background terminals"),
            ("", "\n\n"),
        ])

    fragments.append((
        "class:ps.meta",
        "  • No background terminals running.",
    ))

    return FragmentBlock(tuple(fragments))


def _exec_stream_snapshot_error_block(
    error: BaseException,
    *,
    terminal_width: int,
    mode: ExecSnapshotMode = "stream",
) -> FragmentBlock:
    """生成后台终端快照读取失败状态块。"""
    detail = _clip_inline(error, max(1, terminal_width - 4))

    header = (
        (
            ("class:prompt.command.slash", "/ps"),
            ("", "\n\n"),
            ("class:ps.title", "Background terminals"),
            ("", "\n\n"),
        )
        if mode == "history"
        else (
            ("class:prompt.command.slash", "/ps"),
            ("class:ps.meta", " · "),
            ("class:ps.title", "Background terminals"),
            ("", "\n\n"),
        )
    )
    return FragmentBlock((*header, (
        "class:ps.stream", f"  • {detail or type(error).__name__}"
    )))


def _execution_backend(
    mind: typing.Any,
    viewer_mode: ProcessViewerMode,
) -> typing.Any:
    """返回当前命令来源对应的本地执行服务。"""
    if viewer_mode == "inline":
        return mind.user_shell
    return mind.native_coding


async def watch_exec_session(
    runtime: "ProcessRuntimePort",
    mind: typing.Any,
    session_id: str | None,
    *,
    announce_detach: bool = False,
    initial_snapshot: dict[str, typing.Any],
    activate_immediately: bool = False,
    viewer_mode: ProcessViewerMode = "process",
    ready_event: asyncio.Event | None = None,
    capture_input: bool = True
) -> bool | str:
    """按指定展示模式持续查看命令会话输出。"""
    sid = str(session_id or "").strip()
    if not sid:
        return False

    application = mind.frontend.application
    execution = _execution_backend(mind, viewer_mode)

    initial = dict(initial_snapshot)

    runtime.cancel_background_session_task(sid)

    if initial.get("ok") is False:
        if ready_event is not None:
            ready_event.set()
        return False

    if str(initial.get("status") or "").strip() == "exited":
        if _belongs_to_current_conversation(mind, initial):
            final_block = (
                exec_session_user_shell_block(
                    initial,
                    terminal_width=application.viewport.width,
                    running=False,
                )
                if viewer_mode == "inline"
                else exec_session_summary_block(
                    initial,
                    terminal_width=application.viewport.width,
                )
            )
            transcript_block = exec_session_transcript_block(initial)
            if viewer_mode == "inline":
                await runtime.start_inline_process(
                    sid,
                    final_block,
                    transcript_block=transcript_block,
                    gap_before=1,
                )
                runtime.commit_inline_process(
                    final_block,
                    session_id=sid,
                    transcript_block=transcript_block,
                )
            else:
                runtime.commit_process_result(
                    final_block,
                    transcript_block=transcript_block,
                )
        else:
            runtime.retain_process_completion(
                initial,
                label=_completion_status_label(initial),
            )

        if ready_event is not None:
            ready_event.set()

        return "exited"

    state: dict[str, typing.Any] = {
        "snapshot": initial,
        "last_snapshot": initial,
        "updated_at": time.time()
    }

    return await _watch_exec_session(
        mind,
        sid,
        state,
        runtime=runtime,
        announce_detach=announce_detach,
        activate_immediately=activate_immediately,
        viewer_mode=viewer_mode,
        ready_event=ready_event,
        capture_input=capture_input,
        execution=execution,
    )


async def watch_user_shell_session(
    runtime: "ProcessRuntimePort",
    mind: typing.Any,
    session_id: str | None,
    *,
    announce_detach: bool = False,
    initial_snapshot: dict[str, typing.Any],
    ready_event: asyncio.Event | None = None,
) -> bool | str:
    """以正文 UserShell 生命周期监视手动命令，不创建进程查看器。"""
    return await watch_exec_session(
        runtime,
        mind,
        session_id,
        announce_detach=announce_detach,
        initial_snapshot=initial_snapshot,
        viewer_mode="inline",
        ready_event=ready_event,
        capture_input=False,
    )


async def _watch_exec_session(
    mind: typing.Any,
    session_id: str,
    state: dict[str, typing.Any],
    *,
    runtime: "ProcessRuntimePort",
    announce_detach: bool,
    activate_immediately: bool,
    viewer_mode: ProcessViewerMode,
    ready_event: asyncio.Event | None,
    capture_input: bool,
    execution: typing.Any,
) -> bool | str:
    """按结构化会话事件更新主 TUI 中的命令执行单元。"""
    application = mind.frontend.application

    if not capture_input:
        try:
            running = await execution.running_exec_sessions()
            _set_exec_status(
                runtime,
                running,
                excluded_session_id=session_id,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            runtime.set_process_status_label("")

    live_block = exec_session_live_block(
        state.get("snapshot"),
        terminal_width=application.viewport.width,
        viewer_mode=viewer_mode,
    )

    transcript_block = exec_session_transcript_block(state.get("snapshot"))

    inline_mode = viewer_mode == "inline"
    animation_task: asyncio.Task[None] | None = None
    if inline_mode:
        viewer_task = await runtime.start_inline_process(
            session_id,
            live_block,
            transcript_block=transcript_block,
            gap_before=1,
        )
        if ready_event is not None:
            ready_event.set()

        async def animate_inline_process() -> None:
            """按终端帧率刷新正文执行单元的活动指示点。"""
            try:
                while not viewer_task.done():
                    current = state.get("snapshot")
                    if not isinstance(current, dict):
                        current = {}
                    if str(current.get("status") or "").strip() == "exited":
                        return None

                    runtime.update_inline_process(
                        exec_session_live_block(
                            current,
                            terminal_width=application.viewport.width,
                            viewer_mode="inline",
                            animated=True,
                        ),
                        session_id=session_id,
                        transcript_block=exec_session_transcript_block(current),
                        gap_before=1,
                    )
                    await asyncio.sleep(SHELL_ACTIVITY_FRAME_SEC)
            except asyncio.CancelledError:
                return None

        animation_task = asyncio.create_task(
            animate_inline_process(),
            name=f"shell activity animation {session_id}",
        )
    else:
        viewer_request = ProcessViewerRequest(
            fragments=PROCESS_VIEWER_FOCUS_REQUEST.fragments,
            max_height=PROCESS_VIEWER_FOCUS_REQUEST.max_height,
            capture_input=capture_input,
            session_id=session_id,
        )
        if activate_immediately:
            viewer_task = runtime.begin_process_viewer(
                viewer_request,
                live_block,
                transcript_block=transcript_block,
                gap_before=2,
            )
            if ready_event is not None:
                ready_event.set()
        else:
            viewer_task = asyncio.create_task(runtime.view_process(
                viewer_request,
                live_block,
                transcript_block=transcript_block,
                ready_event=ready_event,
            ))

    async def poll() -> None:
        rendered_block = live_block
        rendered_transcript = transcript_block

        while not viewer_task.done():
            update_event = await _wait_for_exec_session_update(
                session_id,
                state.get("snapshot") or {},
                execution=execution,
            )
            if not update_event or viewer_task.done():
                continue

            current_snapshot = update_event.get("snapshot")
            if not isinstance(current_snapshot, dict):
                raise RuntimeError(
                    "exec session update did not include a snapshot"
                )
            delta_items = update_event.get("delta")
            if not isinstance(delta_items, list):
                raise RuntimeError(
                    "exec session update did not include delta items"
                )
            if (
                not delta_items
                and str(update_event.get("event") or "") != "completed"
            ):
                state["snapshot"] = current_snapshot
                continue
            if current_snapshot.get("ok") is False:
                if inline_mode:
                    runtime.resolve_inline_process(
                        state.get("last_snapshot"),
                        session_id=session_id,
                    )
                else:
                    runtime.resolve_process_viewer(state.get("last_snapshot"))
                return None

            state["snapshot"]      = current_snapshot
            state["updated_at"]    = time.time()
            state["last_snapshot"] = current_snapshot

            updated_block = exec_session_live_block(
                current_snapshot,
                terminal_width=application.viewport.width,
                viewer_mode=viewer_mode,
            )

            updated_transcript = exec_session_transcript_block(current_snapshot)

            if (
                updated_block != rendered_block
                or updated_transcript != rendered_transcript
            ):
                if inline_mode:
                    runtime.update_inline_process(
                        updated_block,
                        session_id=session_id,
                        transcript_block=updated_transcript,
                        gap_before=1,
                    )
                elif activate_immediately:
                    runtime.update_process_viewer(
                        updated_block,
                        transcript_block=updated_transcript,
                        gap_before=2,
                    )
                else:
                    runtime.update_process_viewer(
                        updated_block,
                        transcript_block=updated_transcript,
                    )

                rendered_block = updated_block
                rendered_transcript = updated_transcript

            if str(current_snapshot.get("status") or "").strip() == "exited":
                if inline_mode:
                    runtime.resolve_inline_process(
                        current_snapshot,
                        session_id=session_id,
                    )
                else:
                    runtime.resolve_process_viewer(current_snapshot)
                return None

    poll_task = asyncio.create_task(poll())

    try:
        result = await viewer_task
    except BaseException:
        if inline_mode:
            runtime.dismiss_inline_process(session_id)
        else:
            runtime.dismiss_process_viewer()
        raise

    finally:
        if not poll_task.done():
            poll_task.cancel()
        await asyncio.gather(poll_task, return_exceptions=True)
        if animation_task is not None and not animation_task.done():
            animation_task.cancel()
        if animation_task is not None:
            await asyncio.gather(animation_task, return_exceptions=True)

    settled: bool = False

    try:
        if result == "interrupt":
            result = await _interrupt_exec_session(
                session_id,
                execution=execution,
            )

        if isinstance(result, dict):
            if _belongs_to_current_conversation(mind, result):
                final_block = (
                    exec_session_user_shell_block(
                        result,
                        terminal_width=application.viewport.width,
                        running=False,
                    )
                    if inline_mode
                    else exec_session_summary_block(
                        result,
                        terminal_width=application.viewport.width,
                    )
                )
                if inline_mode:
                    runtime.commit_inline_process(
                        final_block,
                        session_id=session_id,
                        transcript_block=exec_session_transcript_block(result),
                    )
                else:
                    runtime.commit_process_viewer(
                        final_block,
                        transcript_block=exec_session_transcript_block(result),
                    )
            else:
                if inline_mode:
                    runtime.dismiss_inline_process(session_id)
                else:
                    runtime.dismiss_process_viewer()
                runtime.retain_process_completion(
                    result,
                    label=_completion_status_label(result),
                )
            settled = True
            return "exited"

        if result == "detach":
            snapshot = state.get("last_snapshot") or state.get("snapshot")
            if announce_detach and isinstance(snapshot, dict):
                detached_block = exec_session_detached_block(
                    snapshot,
                    terminal_width=application.viewport.width,
                )
                if inline_mode:
                    runtime.commit_inline_process(
                        detached_block,
                        session_id=session_id,
                        transcript_block=exec_session_transcript_block(snapshot),
                        retain_for_background=True,
                    )
                    runtime.mark_inline_process_background(session_id)
                    await execution.mark_exec_session_background(session_id)
                else:
                    runtime.commit_process_viewer(
                        detached_block,
                        transcript_block=exec_session_transcript_block(snapshot),
                    )
            else:
                if inline_mode:
                    runtime.dismiss_inline_process(session_id)
                else:
                    runtime.dismiss_process_viewer()
            settled = True
            if isinstance(snapshot, dict) and snapshot.get("origin") == "tui_shell":
                runtime.start_background_session_task(
                    session_id,
                    _watch_detached_exec_session(
                        runtime,
                        mind,
                        session_id,
                        execution=execution,
                    ),
                )
            return "detach"

        if inline_mode:
            runtime.dismiss_inline_process(session_id)
        else:
            runtime.dismiss_process_viewer()
        settled = True

        return True

    finally:
        if not settled:
            if inline_mode:
                runtime.dismiss_inline_process(session_id)
            else:
                runtime.dismiss_process_viewer()


def render_exec_session_panel(
    state: dict[str, typing.Any],
    *,
    height: int,
    terminal_width: int | None = None,
    viewer_mode: ProcessViewerMode = "process",
    animated: bool = False,
) -> StyleAndTextTuples:
    """生成进程会话查看面板内容。"""
    snapshot = state.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}

    body_height = max(1, height - 2)
    width       = _terminal_width(terminal_width)

    if viewer_mode == "inline":
        return list(exec_session_user_shell_block(
            snapshot,
            terminal_width=width,
            running=str(snapshot.get("status") or "").strip() != "exited",
            animated=animated,
        ).fragments)

    title = _panel_title_fragments(snapshot, terminal_width=width)

    help_text = _clip_inline(
        "Enter/Esc/q background · Ctrl+C stop · output is tailed",
        width,
    )

    lines: StyleAndTextTuples = [
        *title,
        ("", "\n"),
        (
            "class:ps.help",
            help_text,
        ),
        ("", "\n"),
    ]

    if snapshot.get("ok") is False:
        error = f"  ■ {snapshot.get('reason') or 'snapshot_failed'}"
        lines.append(("class:ps.error", _clip_inline(error, width)))
        lines.append(("", "\n"))
        return lines

    visible = _panel_output_lines(snapshot, limit=body_height)
    if visible:
        for line in visible:
            lines.append(("class:ps.meta", "  "))
            lines.append(("class:ps.output", _clip_inline(line, width - 4)))
            lines.append(("", "\n"))
    else:
        lines.append(("class:ps.waiting", "(waiting for output)"))
        lines.append(("", "\n"))

    return lines


def exec_session_live_block(
    snapshot: typing.Any,
    *,
    terminal_width: int | None = None,
    viewer_mode: ProcessViewerMode = "process",
    animated: bool = False,
) -> FragmentBlock:
    """生成进程运行期间的动态正文块。"""
    current = snapshot if isinstance(snapshot, dict) else {}

    fragments = render_exec_session_panel(
        {"snapshot": current},
        height=PS_VISIBLE_OUTPUT_LINES + 2,
        terminal_width=terminal_width,
        viewer_mode=viewer_mode,
        animated=animated,
    )

    return FragmentBlock(tuple(fragments))


def exec_session_user_shell_block(
    snapshot: typing.Any,
    *,
    terminal_width: int | None = None,
    running: bool | None = None,
    animated: bool = False,
) -> FragmentBlock:
    """生成手动 Shell 的瀑布式执行单元。"""
    current = snapshot if isinstance(snapshot, dict) else {}
    width   = _terminal_width(terminal_width)

    snapshot_failed = current.get("ok") is False

    is_running = (
        running
        if running is not None
        else str(current.get("status") or "").strip() != "exited"
    )

    if snapshot_failed:
        is_running = False

    exit_code = current.get("exit_code")

    failed = snapshot_failed or (
        not is_running and exit_code not in (None, 0)
    )

    if is_running and animated:
        dot_style, dot_glyph = status_indicator_fragment(
            time.perf_counter(),
            family="wait",
            animated=True,
        )
    else:
        dot_style = (
            "class:shell.title.dot.running"
            if is_running
            else "class:shell.title.dot.failure"
            if failed
            else "class:shell.title.dot.success"
        )
        dot_glyph = "•"

    title_style   = "class:shell.title.action"
    command_style = "class:shell.title.command"

    title = "Running" if is_running else "You ran"

    command = _clip_inline(
        current.get("command") or "command",
        max(
            1,
            width
            - get_cwidth(f"• {title} "),
        ),
    )

    fragments: list[tuple[str, str]] = [
        (dot_style, dot_glyph),
        ("", " "),
        (title_style, title),
        ("", " "),
        (command_style, command),
    ]

    if snapshot_failed:
        output_lines, omitted = [
            f"■ {current.get('reason') or 'snapshot_failed'}"
        ], 0
    else:
        output_lines, omitted = _shell_output_lines(
            current,
            limit=SHELL_VISIBLE_OUTPUT_LINES,
            width=max(1, width - 4),
        )
        if not output_lines:
            output_lines = [
                "(waiting for output)" if is_running else "(no output)"
            ]

    head_count = max(1, (SHELL_VISIBLE_OUTPUT_LINES - 1) // 2)
    has_dropped_prefix = bool(
        int(current.get("output_lines_dropped") or 0) > 0
    )
    ellipsis_index = (
        0
        if has_dropped_prefix
        else min(head_count, len(output_lines))
    )
    ellipsis_added = False

    for index, line in enumerate(output_lines):
        if omitted and index == ellipsis_index:
            fragments.extend([
                ("", "\n"),
                ("class:ps.output", "    "),
                ("class:ps.output", f"… +{omitted} lines"),
            ])
            ellipsis_added = True
        fragments.extend([
            ("", "\n"),
            (
                "class:ps.output",
                "  └ " if index == 0 else "    ",
            ),
            ("class:ps.output", _clip_inline(line, max(1, width - 4))),
        ])

    if omitted and not ellipsis_added:
        fragments.extend([
            ("", "\n"),
            ("class:ps.output", "    "),
            ("class:ps.output", f"… +{omitted} lines"),
        ])

    return FragmentBlock(tuple(fragments))


def exec_session_transcript_block(snapshot: typing.Any) -> FragmentBlock:
    """生成包含完整命令和已保留输出的进程记录块。"""
    current = snapshot if isinstance(snapshot, dict) else {}

    command = str(
        current.get("command")
        or current.get("session_id")
        or "command"
    ).strip()

    output = current.get("output")
    if output is None:
        raw_lines = current.get("output_lines")
        output = (
            "\n".join(str(line) for line in raw_lines)
            if isinstance(raw_lines, list)
            else ""
        )

    fragments: list[tuple[str, str]] = [
        ("class:prompt.command.slash", "$ "),
        ("class:prompt.command", command),
    ]

    output_text = str(output or "").strip("\n")
    if output_text:
        fragments.extend([
            ("", "\n"),
            ("class:ps.output", output_text),
        ])

    return FragmentBlock(tuple(fragments))


def exec_session_summary_block(
    snapshot: dict[str, typing.Any],
    *,
    terminal_width: int | None = None
) -> FragmentBlock:
    """生成前台命令结束后的稳定摘要块。"""
    if snapshot.get("origin") == "tui_shell":
        return exec_session_user_shell_block(
            snapshot,
            terminal_width=terminal_width,
            running=False,
        )

    return command_summary_text(
        exec_session_command_summary(snapshot),
        terminal_width=terminal_width,
        line_prefix=_summary_line_prefix(snapshot),
        first_line_prefix=_summary_first_line_prefix(snapshot),
    )


def exec_session_detached_block(
    snapshot: dict[str, typing.Any],
    *,
    terminal_width: int | None = None
) -> FragmentBlock:
    """生成命令转入后台后的稳定摘要块。"""
    if snapshot.get("origin") == "tui_shell":
        return exec_session_user_shell_block(
            snapshot,
            terminal_width=terminal_width,
        )

    session_id = str(snapshot.get("session_id") or "").strip()

    summary = CommandSummary(
        kind=_session_kind(snapshot),
        command=str(snapshot.get("command") or session_id or "command"),
        suffix=f" · {session_id}",
        lines=tuple(exec_session_summary_lines(snapshot)),
    )

    return command_summary_text(
        summary,
        terminal_width=terminal_width,
        line_prefix=_summary_line_prefix(snapshot),
        first_line_prefix=_summary_first_line_prefix(snapshot),
    )


def exec_session_command_summary(snapshot: dict[str, typing.Any]) -> CommandSummary:
    """把 exec_command 快照转换为统一命令摘要。"""
    return CommandSummary(
        kind=_session_kind(snapshot),
        command=str(snapshot.get("command") or snapshot.get("session_id") or "exec_command"),
        suffix=_exec_session_status_suffix(snapshot),
        lines=tuple(exec_session_summary_lines(snapshot))
    )


def exec_session_summary_title_parts(
    snapshot: dict[str, typing.Any],
    *,
    terminal_width: int | None = None
) -> list[tuple[str, str]]:
    """返回 exec_command 摘要标题的分段样式。"""
    return command_summary_title_parts(
        exec_session_command_summary(snapshot),
        terminal_width=terminal_width
    )


def exec_session_summary_lines(
    snapshot: dict[str, typing.Any],
    *,
    max_lines: int = PS_VISIBLE_OUTPUT_LINES
) -> list[str]:
    """返回 exec_command 摘要输出行。"""
    limit = max(1, int(max_lines or PS_VISIBLE_OUTPUT_LINES))

    display = [
        _clip_inline(line, 160)
        for line in _panel_output_lines(snapshot, limit=limit)
    ]
    if display:
        return display[-limit:]

    exit_code = snapshot.get("exit_code")
    if exit_code not in (None, 0):
        return [
            f"{_session_kind(snapshot)} exited with code {int(exit_code or 0)}"
        ]

    return ["(no output)"]


def _running_items(snapshot: typing.Any) -> list[dict[str, typing.Any]]:
    """从运行中会话快照提取可选项。"""
    if not isinstance(snapshot, dict):
        return []

    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []

    return [item for item in raw_items if isinstance(item, dict)]


def _result_items(
    result: typing.Any,
    key: str
) -> list[dict[str, typing.Any]]:
    """从批量操作结果提取结构化项目。"""
    if not isinstance(result, dict):
        return []

    items = result.get(key)

    if not isinstance(items, list):
        return []

    return [item for item in items if isinstance(item, dict)]


async def _interrupt_exec_session(
    session_id: str,
    *,
    execution: typing.Any,
) -> dict[str, typing.Any]:
    """中断进程会话并返回收束后快照。"""
    snapshot = await execution.control_exec_session(
        session_id=session_id,
        control="interrupt",
    )
    if snapshot.get("ok") is False:
        return snapshot
    if str(snapshot.get("status") or "") == "exited":
        return snapshot

    await asyncio.sleep(PS_INTERRUPT_GRACE_SEC)
    snapshot = await execution.exec_session_output_snapshot(
        session_id=session_id,
        max_output_chars=PS_OUTPUT_LIMIT,
    )
    if snapshot.get("ok") is False:
        return snapshot
    if str(snapshot.get("status") or "") == "exited":
        return snapshot

    return await execution.control_exec_session(
        session_id=session_id,
        control="kill",
    )


async def _watch_detached_exec_session(
    runtime: "ProcessRuntimePort",
    mind: typing.Any,
    session_id: str,
    *,
    execution: typing.Any,
) -> None:
    """按会话事件等待后台终端退出并提交一次完成摘要。"""
    snapshot = await execution.exec_session_output_snapshot(
        session_id=session_id,
        max_output_chars=PS_OUTPUT_LIMIT,
    )

    while True:
        if snapshot.get("ok") is False:
            return None

        if str(snapshot.get("status") or "").strip() == "exited":
            await runtime.wait_for_process_routing_boundary()
            if _belongs_to_current_conversation(mind, snapshot):
                final_block = exec_session_summary_block(
                    snapshot,
                    terminal_width=mind.frontend.application.viewport.width,
                )
                transcript_block = exec_session_transcript_block(snapshot)
                replaced = runtime.replace_detached_inline_process(
                    session_id,
                    final_block,
                    transcript_block=transcript_block,
                )
                if not replaced:
                    raise RuntimeError(
                        "detached UserShell cell is unavailable"
                    )
            else:
                runtime.retain_process_completion(
                    snapshot,
                    label=_completion_status_label(snapshot),
                )

            return None

        update = await _wait_for_exec_session_update(
            session_id,
            snapshot,
            execution=execution,
        )
        if not isinstance(update, dict):
            continue

        current_snapshot = update.get("snapshot")
        if isinstance(current_snapshot, dict):
            snapshot = current_snapshot


async def _wait_for_exec_session_update(
    session_id: str,
    snapshot: dict[str, typing.Any],
    *,
    execution: typing.Any,
) -> dict[str, typing.Any] | None:
    """等待结构化会话事件，返回事件载荷或空值。"""
    result = await execution.wait_exec_session_update(
        session_id=session_id,
        revision=int(snapshot.get("revision") or 0),
        timeout_sec=PS_EVENT_WAIT_TIMEOUT_SEC,
    )
    if not isinstance(result, dict) or not result.get("changed"):
        return None
    return result


def _belongs_to_current_conversation(
    controller: typing.Any,
    snapshot: dict[str, typing.Any]
) -> bool:
    """判断进程完成摘要是否仍属于当前对话。"""
    owner = (
        str(snapshot.get("owner_cid") or "").strip(),
        str(snapshot.get("owner_sid") or "").strip(),
    )
    if not all(owner):
        return True

    conversation    = getattr(controller, "conversation", None)
    snapshot_method = getattr(conversation, "snapshot", None)

    if conversation is None or not callable(snapshot_method):
        return False

    current = snapshot_method()
    if not isinstance(current, dict):
        return False

    return owner == (
        str(current.get("cid") or "").strip(),
        str(current.get("sid") or "").strip(),
    )


def _completion_status_label(snapshot: dict[str, typing.Any]) -> str:
    """生成后台进程完成后使用的单行状态。"""
    command = _inline_text(
        snapshot.get("command") or snapshot.get("session_id") or "command"
    )
    return f"{command} {_completion_state(snapshot)}"


def _completion_state(snapshot: dict[str, typing.Any]) -> str:
    """返回进程退出状态对应的简短展示名称。"""
    exit_code = snapshot.get("exit_code")
    if exit_code == 0:
        return "completed"
    if exit_code in {130, -2}:
        return "interrupted"

    return "failed"


def _session_kind(snapshot: dict[str, typing.Any]) -> str:
    """返回会话来源对应的展示名称。"""
    return "Shell" if snapshot.get("origin") == "tui_shell" else "Exec"


def _summary_first_line_prefix(snapshot: dict[str, typing.Any]) -> str:
    """返回会话摘要首行的层级前缀。"""
    return "  └ " if snapshot.get("origin") == "tui_shell" else "  "


def _summary_line_prefix(snapshot: dict[str, typing.Any]) -> str:
    """返回会话摘要后续行的层级前缀。"""
    return "    " if snapshot.get("origin") == "tui_shell" else "  "


def _panel_title_fragments(
    snapshot: dict[str, typing.Any],
    *,
    terminal_width: int
) -> StyleAndTextTuples:
    """生成查看面板的分段样式标题。"""
    status = _inline_text(snapshot.get("status")) or "unknown"
    pid    = _inline_text(snapshot.get("pid")) or "-"

    fragments: StyleAndTextTuples = [
        ("class:shell.title.action", _session_kind(snapshot)),
        ("class:ps.meta", f" {status}"),
    ]
    fields: list[tuple[str, str]] = [
        ("class:ps.meta", f"pid={pid}"),
    ]

    sid = _inline_text(snapshot.get("session_id"))
    if sid:
        fields.append(("class:ps.meta", sid))

    exit_code = snapshot.get("exit_code")
    if exit_code is not None:
        fields.append(("class:ps.meta", f"exit={_inline_text(exit_code)}"))
    if bool(snapshot.get("truncated")):
        fields.append(("class:ps.warning", "truncated=true"))

    command = _inline_text(snapshot.get("command"))
    if command:
        fields.append(("class:ps.command", command))

    for field in fields:
        fragments.append(("class:ps.separator", " · "))
        fragments.append(field)

    return _clip_panel_title(fragments, width=terminal_width)


def _clip_panel_title(
    fragments: StyleAndTextTuples,
    *,
    width: int
) -> StyleAndTextTuples:
    """裁剪标题片段并使省略标记继承末尾字段样式。"""
    limit = max(0, int(width))
    text  = "".join(value for _style, value in fragments)

    if get_cwidth(text) <= limit:
        return fragments

    omitted = "…"

    omitted_width = get_cwidth(omitted)

    if limit <= omitted_width:
        return [("class:ps.meta", omitted)] if limit else []

    clipped = clip_fragments(
        list(fragments),
        width=limit - omitted_width,
    )
    if not clipped:
        return [("class:ps.meta", omitted)]

    style, text = clipped[-1]

    clipped[-1] = style, f"{text}{omitted}"

    return clipped


def _exec_session_status_suffix(snapshot: dict[str, typing.Any]) -> str:
    """返回 exec_command 摘要标题状态后缀。"""
    exit_code = snapshot.get("exit_code")

    if exit_code not in (None, 0):
        return f" · exit {int(exit_code or 0)}"
    if str(snapshot.get("status") or "").strip() not in {"", "exited"}:
        return f" · {snapshot.get('status')}"

    return ""


def _panel_output_lines(
    snapshot: dict[str, typing.Any],
    *,
    limit: int
) -> list[str]:
    """提取面板需要展示的输出行。"""
    raw_lines = snapshot.get("output_lines")

    lines = (
        [str(line) for line in raw_lines if str(line).strip()]
        if isinstance(raw_lines, list)
        else []
    )

    if not lines:
        output = str(snapshot.get("output") or "")
        lines = [line for line in output.splitlines() if line.strip()]

    return lines[-max(1, int(limit or 1)):]


def _shell_output_lines(
    snapshot: dict[str, typing.Any],
    *,
    limit: int,
    width: int | None = None,
) -> tuple[list[str], int]:
    """按终端显示行提取手动 Shell 的头尾输出并返回省略行数。"""
    raw_lines = snapshot.get("output_lines")
    if isinstance(raw_lines, list) and raw_lines:
        lines = [str(line) for line in raw_lines]
    else:
        output = str(snapshot.get("output") or "")
        lines = output.splitlines()

    if isinstance(width, int) and width > 0:
        wrapped: list[str] = []
        for line in lines:
            wrapped.extend(_wrap_shell_line(line, width))
        lines = wrapped

    max_lines = max(1, int(limit or 1))
    omitted = max(0, int(snapshot.get("output_lines_dropped") or 0))
    if len(lines) <= max_lines:
        return lines, omitted

    if max_lines == 1:
        return [lines[-1]], omitted + len(lines) - 1

    omitted += len(lines) - max_lines + 1
    head_count = max(1, (max_lines - 1) // 2)
    tail_count = max(1, max_lines - head_count - 1)
    return [*lines[:head_count], *lines[-tail_count:]], omitted


def _wrap_shell_line(value: typing.Any, width: int) -> list[str]:
    """把单条 Shell 输出按终端显示宽度拆成多行。"""
    text = sanitize_terminal_text(value)
    if not text:
        return [""]

    max_width = max(1, int(width or 1))
    lines: list[str] = []
    current: list[str] = []
    current_width = 0

    for character in text:
        character_width = max(0, get_cwidth(character))
        if current and current_width + character_width > max_width:
            lines.append("".join(current))
            current = []
            current_width = 0

        current.append(character)
        current_width += character_width

    if current:
        lines.append("".join(current))
    return lines or [""]


def _terminal_width(terminal_width: int | None = None) -> int:
    """返回当前终端宽度。"""
    if isinstance(terminal_width, int) and terminal_width > 0:
        return terminal_width
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def _clip_inline(value: typing.Any, limit: int) -> str:
    """裁剪单行文本。"""
    return clip_text(_inline_text(value), width=max(1, int(limit or 1)))


def _inline_text(value: typing.Any) -> str:
    """把任意值整理为单行展示文本。"""
    return " ".join(sanitize_terminal_text(value).split())


if __name__ == '__main__':
    pass
