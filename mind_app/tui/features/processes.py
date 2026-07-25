# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import shutil
import typing
import asyncio
from prompt_toolkit.formatted_text import StyleAndTextTuples
from mind_app.presentation.models import TextSpan
from mind_nova import const
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from ..core.models import (
    FragmentBlock,
    MenuOption,
    MenuRequest
)
from ..core.process_viewer import ProcessViewerRequest
from ..core.render import clip_text
from .context import exec_status_display_label
from .summary import (
    CommandSummary,
    command_summary_text,
    command_summary_title_parts,
    render_command_summary
)
from ..core.styles import (
    BRIGHT_STYLE,
    MUTED_STYLE,
    fragment_block
)

PS_PANEL_TICK_SEC: float         = 0.12
PS_OUTPUT_LIMIT: int             = 60000
PS_VISIBLE_OUTPUT_LINES: int     = 8
PS_MENU_VISIBLE_LIMIT: int       = 8
PROCESS_STATUS_ACTIVE_SEC: float = 0.5
PROCESS_STATUS_IDLE_SEC: float   = 1.0

PROCESS_VIEWER_FOCUS_REQUEST = ProcessViewerRequest(
    fragments=(("", " "),),
    max_height=1,
)

_STOP_ALL_ACTION = object()

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime


async def monitor_exec_status(
    runtime: "TuiRuntime",
    mind: typing.Any,
) -> None:
    """同步后台命令会话摘要到 TUI 专属状态行。"""
    try:
        while not mind.task_event.is_set():
            delay = PROCESS_STATUS_IDLE_SEC
            try:
                snapshot = await mind.native_coding.running_exec_sessions()
                sessions = _running_items(snapshot)
                label = exec_status_display_label(
                    snapshot,
                    line_width=runtime.terminal_width,
                )
                delay = (
                    PROCESS_STATUS_ACTIVE_SEC
                    if sessions
                    else PROCESS_STATUS_IDLE_SEC
                )
                runtime.set_process_status_label(label)
            except (OSError, RuntimeError, TypeError, ValueError):
                pass
            await asyncio.sleep(delay)
    finally:
        runtime.set_process_status_label("")


async def manage_exec_sessions(
    runtime: "TuiRuntime",
    mind: typing.Any,
) -> bool:
    """在主 TUI 中查看或停止后台命令会话。"""
    application = mind.frontend.application
    snapshot    = await mind.native_coding.running_exec_sessions()
    sessions    = _running_items(snapshot)

    if not sessions:
        render_no_background_commands(application)
        return True

    selection = await runtime.select_menu(MenuRequest(
        title="Background Commands",
        status=f"running={len(sessions)}",
        help_text="Up/Down select · Enter choose · Esc/q close",
        options=(
            *(
                MenuOption(
                    value=str(item.get("session_id") or "").strip() or None,
                    label=_inline_text(item.get("command")) or "(unknown command)",
                    detail=(
                        f"{_origin_label(item.get('origin'))} "
                        f"pid={item.get('pid') or '-'}"
                    ),
                )
                for item in sessions
            ),
            MenuOption(
                value=_STOP_ALL_ACTION,
                label="Stop all background commands",
                detail=(
                    f"terminate {len(sessions)} "
                    f"process {'tree' if len(sessions) == 1 else 'trees'}"
                ),
            ),
        ),
    ))

    if selection is None:
        return False
    if selection is _STOP_ALL_ACTION:
        return await stop_all_exec_sessions(runtime, mind, sessions=sessions)

    selected_session = next(
        (
            item for item in sessions
            if str(item.get("session_id") or "").strip() == str(selection)
        ),
        None,
    )

    return bool(await watch_exec_session(
        runtime,
        mind,
        str(selection),
        initial_snapshot=selected_session,
        activate_immediately=True,
    ))


async def stop_all_exec_sessions(
    runtime: "TuiRuntime",
    mind: typing.Any,
    *,
    sessions: list[dict[str, typing.Any]] | None = None,
) -> bool:
    """确认并停止当前全部后台命令会话。"""
    application = mind.frontend.application
    if sessions is None:
        snapshot = await mind.native_coding.running_exec_sessions()
        sessions = _running_items(snapshot)

    if not sessions:
        render_no_background_commands(application)
        return True

    count        = len(sessions)
    command_noun = "command" if count == 1 else "commands"
    tree_noun    = "process tree" if count == 1 else "process trees"

    confirmed = await runtime.select_menu(MenuRequest(
        title="Stop Background Commands",
        status=f"running={count}",
        body=(
            f"Stop all {count} {const.APP_DESC}-owned background {tree_noun}?",
        ),
        help_text="Up/Down select · Enter choose · Esc/q cancel",
        options=(
            MenuOption(
                value=False,
                label="Cancel",
                detail="keep background commands running",
            ),
            MenuOption(
                value=True,
                label=f"Stop {count} {command_noun}",
                detail="terminate every listed process tree",
            ),
        ),
        selected=0,
    ))

    if confirmed is not True:
        return False

    result = await mind.native_coding.stop_exec_sessions()

    for item in _result_items(result, "items"):
        runtime.cancel_background_session_task(item.get("session_id"))

    runtime.set_process_status_label("")
    render_exec_sessions_stopped(application, result)
    return True


def render_no_background_commands(application: ApplicationSink) -> None:
    """渲染当前没有后台命令的状态。"""
    application.emit(ApplicationView(
        type="tui.exec.empty",
        renderable=fragment_block(
            TextSpan("Background commands", BRIGHT_STYLE),
            TextSpan("\n\n  • ", MUTED_STYLE),
            TextSpan("No background commands running.", MUTED_STYLE),
        ),
    ))


def render_exec_sessions_stopped(
    application: ApplicationSink,
    result: typing.Any,
) -> None:
    """渲染批量停止后台命令的结果。"""
    data      = result if isinstance(result, dict) else {}
    requested = int(data.get("requested") or 0)
    stopped   = int(data.get("stopped") or 0)
    failed    = int(data.get("failed") or 0)
    details = [f"requested={requested} · stopped={stopped} · failed={failed}"]

    details.extend(
        "failed "
        f"pid={item.get('pid') or '-'} "
        f"{_inline_text(item.get('command')) or '(unknown command)'} · "
        f"{item.get('reason') or 'stop_failed'}"
        for item in _result_items(data, "failures")[:5]
    )
    lines = tuple(
        f"{'└' if index == len(details) - 1 else '├'} {line}"
        for index, line in enumerate(details)
    )
    render_command_summary(application, CommandSummary(
        kind="Processes",
        command="stop all background commands",
        suffix=" · complete" if failed == 0 else " · partial",
        lines=lines,
    ))


async def watch_exec_session(
    runtime: "TuiRuntime",
    mind: typing.Any,
    session_id: str | None,
    *,
    announce_detach: bool = False,
    initial_snapshot: dict[str, typing.Any] | None = None,
    activate_immediately: bool = False
) -> bool | str:
    """在主 TUI 中持续查看命令会话输出。"""
    sid = str(session_id or "").strip()
    if not sid:
        return False

    application = mind.frontend.application

    if initial_snapshot is None:
        initial = await mind.native_coding.exec_session_output_snapshot(
            session_id=sid,
            max_output_chars=PS_OUTPUT_LIMIT,
        )
    else:
        initial = dict(initial_snapshot)
        initial.setdefault("ok", True)
        initial.setdefault("status", "running")
        initial.setdefault("output_lines", [])

    runtime.cancel_background_session_task(sid)

    if initial.get("ok") is False:
        return False
    if str(initial.get("status") or "").strip() == "exited":
        runtime.commit_process_result(exec_session_summary_block(
            initial,
            terminal_width=application.viewport.width,
        ))
        return "exited"

    state: dict[str, typing.Any] = {
        "snapshot": initial,
        "last_snapshot": initial,
        "updated_at": time.time(),
    }
    return await _watch_exec_session(
        mind,
        sid,
        state,
        runtime=runtime,
        announce_detach=announce_detach,
        activate_immediately=activate_immediately,
    )


async def _watch_exec_session(
    mind: typing.Any,
    session_id: str,
    state: dict[str, typing.Any],
    *,
    runtime: "TuiRuntime",
    announce_detach: bool,
    activate_immediately: bool
) -> bool | str:
    """轮询并更新主 TUI 中的命令会话面板。"""
    application = mind.frontend.application

    live_block = exec_session_live_block(
        state.get("snapshot"),
        terminal_width=application.viewport.width,
    )

    if activate_immediately:
        viewer_task = runtime.begin_process_viewer(
            PROCESS_VIEWER_FOCUS_REQUEST,
            live_block,
        )
    else:
        viewer_task = asyncio.create_task(runtime.view_process(
            PROCESS_VIEWER_FOCUS_REQUEST,
            live_block,
        ))

    async def poll() -> None:
        while not viewer_task.done():
            current_snapshot = await mind.native_coding.exec_session_output_snapshot(
                session_id=session_id,
                max_output_chars=PS_OUTPUT_LIMIT,
            )
            if current_snapshot.get("ok") is False:
                runtime.resolve_process_viewer(state.get("last_snapshot"))
                return None

            state["snapshot"]      = current_snapshot
            state["updated_at"]    = time.time()
            state["last_snapshot"] = current_snapshot

            runtime.update_process_viewer(exec_session_live_block(
                current_snapshot,
                terminal_width=application.viewport.width,
            ))

            if str(current_snapshot.get("status") or "").strip() == "exited":
                runtime.resolve_process_viewer(current_snapshot)
                return None
            await asyncio.sleep(PS_PANEL_TICK_SEC)

    poll_task = asyncio.create_task(poll())

    try:
        result = await viewer_task
    except BaseException:
        runtime.dismiss_process_viewer()
        raise

    finally:
        if not poll_task.done():
            poll_task.cancel()
        await asyncio.gather(poll_task, return_exceptions=True)

    settled: bool = False

    try:
        if result == "interrupt":
            result = await _interrupt_exec_session(mind, session_id)
        if isinstance(result, dict):
            runtime.commit_process_viewer(exec_session_summary_block(
                result,
                terminal_width=application.viewport.width,
            ))
            settled = True
            return "exited"
        if result == "detach":
            snapshot = state.get("last_snapshot") or state.get("snapshot")
            if announce_detach and isinstance(snapshot, dict):
                runtime.commit_process_viewer(exec_session_detached_block(
                    snapshot,
                    terminal_width=application.viewport.width,
                ))
            else:
                runtime.dismiss_process_viewer()
            settled = True
            if isinstance(snapshot, dict) and snapshot.get("origin") == "tui_shell":
                runtime.start_background_session_task(
                    session_id,
                    _watch_detached_exec_session(runtime, mind, session_id),
                )
            return "detach"
        runtime.dismiss_process_viewer()
        settled = True
        return True
    finally:
        if not settled:
            runtime.dismiss_process_viewer()


def render_exec_session_menu(
    sessions: list[dict[str, typing.Any]],
    selected: int,
    *,
    terminal_width: int | None = None
) -> StyleAndTextTuples:
    """生成 exec_command 会话菜单内容。"""
    width         = _terminal_width(terminal_width)
    command_width = max(12, width - 30)

    start, visible = _visible_session_window(sessions, selected)

    end = start + len(visible)

    lines: StyleAndTextTuples = [
        ("class:ps.title", "Background Commands"),
        ("", "\n"),
        ("class:ps.status", _session_menu_status(len(sessions), start, end)),
        ("", "\n"),
        ("class:ps.help", "↑/↓ select · Enter view · q close"),
        ("", "\n"),
    ]

    for visible_index, item in enumerate(visible):
        index  = start + visible_index
        active = index == selected

        prefix_style = "class:ps.index.active" if active else "class:ps.index"
        row_style    = "class:ps.active" if active else "class:ps.command"
        marker       = ">" if active else " "
        pid          = str(item.get("pid") or "-")
        command      = _clip_inline(item.get("command"), command_width)

        lines.extend([
            (prefix_style, f"{marker} {index + 1} "),
            ("class:ps.pid", f" pid={pid.rjust(5)}  "),
            (row_style, command),
            ("", "\n"),
        ])

    return lines


def render_exec_session_panel(
    state: dict[str, typing.Any],
    *,
    height: int,
    terminal_width: int | None = None
) -> StyleAndTextTuples:
    """生成 exec_command 会话查看面板内容。"""
    snapshot = state.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}

    body_height = max(1, height - 4)
    width       = _terminal_width(terminal_width)

    title = _clip_inline(
        _panel_title(snapshot, terminal_width=width),
        width,
    )

    meta = _clip_inline(
        _panel_meta(snapshot, terminal_width=width),
        width,
    )

    help_text = _clip_inline(
        "Enter/Esc/q background · Ctrl+C stop · output is tailed",
        width,
    )

    lines: StyleAndTextTuples = [
        ("class:ps.title", title),
        ("", "\n"),
        ("class:ps.meta", meta),
        ("", "\n"),
        (
            "class:ps.help",
            help_text,
        ),
        ("", "\n"),
    ]

    if snapshot.get("ok") is False:
        error = f"  {snapshot.get('reason') or 'snapshot_failed'}"
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
    terminal_width: int | None = None
) -> FragmentBlock:
    """生成前台命令运行期间的动态正文块。"""
    current = snapshot if isinstance(snapshot, dict) else {}

    fragments = render_exec_session_panel(
        {"snapshot": current},
        height=PS_VISIBLE_OUTPUT_LINES + 4,
        terminal_width=terminal_width,
    )
    return FragmentBlock(tuple(fragments))


def exec_session_summary_block(
    snapshot: dict[str, typing.Any],
    *,
    terminal_width: int | None = None
) -> FragmentBlock:
    """生成前台命令结束后的稳定摘要块。"""
    return command_summary_text(
        exec_session_command_summary(snapshot),
        terminal_width=terminal_width,
    )


def exec_session_detached_block(
    snapshot: dict[str, typing.Any],
    *,
    terminal_width: int | None = None,
) -> FragmentBlock:
    """生成命令转入后台后的稳定摘要块。"""
    session_id = str(snapshot.get("session_id") or "").strip()

    summary = CommandSummary(
        kind=_session_kind(snapshot),
        command=str(snapshot.get("command") or session_id or "command"),
        suffix=f" · {session_id}",
        lines=tuple(exec_session_summary_lines(snapshot)),
    )

    return command_summary_text(summary, terminal_width=terminal_width)


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
    key: str,
) -> list[dict[str, typing.Any]]:
    """从批量操作结果提取结构化项目。"""
    if not isinstance(result, dict):
        return []
    items = result.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


async def _interrupt_exec_session(
    mind: typing.Any,
    session_id: str,
) -> dict[str, typing.Any]:
    """中断进程会话并返回收束后快照。"""
    await mind.native_coding.control_exec_session(
        session_id=session_id,
        control="interrupt",
    )
    for _index in range(8):
        await asyncio.sleep(0.1)
        snapshot = await mind.native_coding.exec_session_output_snapshot(
            session_id=session_id,
            max_output_chars=PS_OUTPUT_LIMIT,
        )
        if snapshot.get("ok") is False:
            return snapshot
        if str(snapshot.get("status") or "") == "exited":
            return snapshot
    await mind.native_coding.control_exec_session(
        session_id=session_id,
        control="terminate",
    )
    return await mind.native_coding.exec_session_output_snapshot(
        session_id=session_id,
        max_output_chars=PS_OUTPUT_LIMIT,
    )


async def _watch_detached_exec_session(
    runtime: "TuiRuntime",
    mind: typing.Any,
    session_id: str,
) -> None:
    """在后台会话退出后提交一次完成摘要。"""
    while True:
        snapshot = await mind.native_coding.exec_session_output_snapshot(
            session_id=session_id,
            max_output_chars=PS_OUTPUT_LIMIT,
        )
        if snapshot.get("ok") is False:
            return None
        if str(snapshot.get("status") or "").strip() == "exited":
            runtime.queue_background_block(command_summary_text(
                exec_session_command_summary(snapshot),
                terminal_width=mind.frontend.application.viewport.width,
            ))
            return None
        await asyncio.sleep(0.25)


def _session_kind(snapshot: dict[str, typing.Any]) -> str:
    """返回会话来源对应的展示名称。"""
    return "Shell" if snapshot.get("origin") == "tui_shell" else "Exec"


def _origin_label(origin: typing.Any) -> str:
    """返回会话来源的简短标签。"""
    return "shell" if str(origin or "") == "tui_shell" else "tool"


def _visible_session_window(
    sessions: list[dict[str, typing.Any]],
    selected: int
) -> tuple[int, list[dict[str, typing.Any]]]:
    """返回当前菜单可见窗口。"""
    total = len(sessions)
    if total <= PS_MENU_VISIBLE_LIMIT:
        return 0, list(sessions)

    active = min(total - 1, max(0, int(selected or 0)))
    half   = PS_MENU_VISIBLE_LIMIT // 2
    start  = active - half
    start  = max(0, min(start, total - PS_MENU_VISIBLE_LIMIT))
    end    = start + PS_MENU_VISIBLE_LIMIT

    return start, list(sessions[start:end])


def _session_menu_status(total: int, start: int, end: int) -> str:
    """返回 exec_command 菜单状态行。"""
    if total <= PS_MENU_VISIBLE_LIMIT:
        return f"running={total}"
    return f"running={total} · showing={start + 1}-{end}"


def _panel_title(
    snapshot: dict[str, typing.Any],
    *,
    terminal_width: int
) -> str:
    """生成查看面板标题。"""
    sid    = str(snapshot.get("session_id") or "").strip()
    status = str(snapshot.get("status") or "unknown").strip()

    kind = _session_kind(snapshot)
    command = _clip_inline(snapshot.get("command"), max(12, terminal_width - 32))
    if command:
        return f"{kind} {status} · {sid} · {command}"

    return f"{kind} {status} · {sid}"


def _panel_meta(
    snapshot: dict[str, typing.Any],
    *,
    terminal_width: int
) -> str:
    """生成查看面板状态行。"""
    pid = snapshot.get("pid")
    cwd = _clip_inline(snapshot.get("cwd"), max(8, terminal_width - 45))

    exit_code = snapshot.get("exit_code")

    parts = [
        f"pid={pid if pid is not None else '-'}",
        f"cwd={cwd or '-'}",
    ]

    if exit_code is not None:
        parts.append(f"exit={exit_code}")
    if bool(snapshot.get("truncated")):
        parts.append("truncated=true")

    return " · ".join(parts)


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
    return " ".join(str(value or "").split())


if __name__ == '__main__':
    pass
