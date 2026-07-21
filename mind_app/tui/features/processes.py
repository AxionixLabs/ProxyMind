# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import shutil
import typing
import asyncio
from prompt_toolkit.formatted_text import StyleAndTextTuples
from mind_app.presentation.models import TextSpan
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from ..core.models import (
    MenuOption,
    MenuRequest
)
from ..core.process_viewer import ProcessViewerRequest
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

PS_PANEL_TICK_SEC: float   = 0.12
PS_OUTPUT_LIMIT: int       = 60000
PS_SUMMARY_MAX_LINES: int  = 8
PS_MENU_VISIBLE_LIMIT: int = 8
PROCESS_STATUS_ACTIVE_SEC: float = 0.5
PROCESS_STATUS_IDLE_SEC: float = 1.0

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


async def choose_exec_session(
    runtime: "TuiRuntime",
    mind: typing.Any,
) -> str | None:
    """在主 TUI 中选择一个运行中的命令会话。"""
    application = mind.frontend.application
    snapshot    = await mind.native_coding.running_exec_sessions()
    sessions    = _running_items(snapshot)

    if not sessions:
        application.emit(ApplicationView(
            type="tui.exec.empty",
            renderable=fragment_block(
                TextSpan("Background terminals", BRIGHT_STYLE),
                TextSpan("\n\n  • ", MUTED_STYLE),
                TextSpan("No background terminals running.", MUTED_STYLE),
            ),
        ))
        application.emit(ApplicationView(type="tui.gap"))
        return None

    return await runtime.select_menu(MenuRequest(
        title="Background Commands",
        status=f"running={len(sessions)}",
        options=tuple(
            MenuOption(
                value=str(item.get("session_id") or "").strip() or None,
                label=_clip_inline(item.get("command"), 80),
                detail=(
                    f"{_origin_label(item.get('origin'))} "
                    f"pid={item.get('pid') or '-'}"
                ),
            )
            for item in sessions
        ),
    ))


async def watch_exec_session(
    runtime: "TuiRuntime",
    mind: typing.Any,
    session_id: str | None,
    *,
    announce_detach: bool = False,
) -> bool | str:
    """在主 TUI 中持续查看命令会话输出。"""
    sid = str(session_id or "").strip()
    if not sid:
        return False

    application = mind.frontend.application
    height      = _ps_panel_height(application.viewport.height)
    initial = await mind.native_coding.exec_session_output_snapshot(
        session_id=sid,
        max_output_chars=PS_OUTPUT_LIMIT,
    )
    runtime.cancel_background_session_task(sid)
    if initial.get("ok") is False:
        return False
    if str(initial.get("status") or "").strip() == "exited":
        render_exec_session_summary(application, initial)
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
        height=height,
        announce_detach=announce_detach,
    )


async def _watch_exec_session(
    mind: typing.Any,
    session_id: str,
    state: dict[str, typing.Any],
    *,
    runtime: "TuiRuntime",
    height: int,
    announce_detach: bool,
) -> bool | str:
    """轮询并更新主 TUI 中的命令会话面板。"""
    application = mind.frontend.application

    def request() -> ProcessViewerRequest:
        fragments = render_exec_session_panel(
            state,
            height=max(4, height - 1),
            terminal_width=application.viewport.width,
        )
        return ProcessViewerRequest(
            fragments=tuple(fragments),
            max_height=height,
        )

    viewer_task = asyncio.create_task(runtime.view_process(request()))

    async def poll() -> None:
        while not viewer_task.done():
            current_snapshot = await mind.native_coding.exec_session_output_snapshot(
                session_id=session_id,
                max_output_chars=PS_OUTPUT_LIMIT,
            )
            if current_snapshot.get("ok") is False:
                runtime.finish_process_viewer(state.get("last_snapshot"))
                return None

            state["snapshot"]      = current_snapshot
            state["updated_at"]    = time.time()
            state["last_snapshot"] = current_snapshot

            runtime.update_process_viewer(request())

            if str(current_snapshot.get("status") or "").strip() == "exited":
                runtime.finish_process_viewer(current_snapshot)
                return None
            await asyncio.sleep(PS_PANEL_TICK_SEC)

    poll_task = asyncio.create_task(poll())

    try:
        result = await viewer_task
    finally:
        if not poll_task.done():
            poll_task.cancel()
        await asyncio.gather(poll_task, return_exceptions=True)

    if result == "interrupt":
        result = await _interrupt_exec_session(mind, session_id)
    elif result == "detach" and announce_detach:
        snapshot = state.get("last_snapshot") or state.get("snapshot")
        if isinstance(snapshot, dict):
            render_exec_session_detached(application, snapshot)
    if isinstance(result, dict):
        render_exec_session_summary(application, result)
        return "exited"
    if result == "detach":
        snapshot = state.get("last_snapshot") or state.get("snapshot")
        if isinstance(snapshot, dict) and snapshot.get("origin") == "tui_shell":
            runtime.start_background_session_task(
                session_id,
                _watch_detached_exec_session(runtime, mind, session_id),
            )
        return "detach"
    return True


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
    title       = _panel_title(snapshot, terminal_width=width)
    meta        = _panel_meta(snapshot, terminal_width=width)

    lines: StyleAndTextTuples = [
        ("class:ps.title", title),
        ("", "\n"),
        ("class:ps.meta", meta),
        ("", "\n"),
        (
            "class:ps.help",
            "Enter/Esc/q background · Ctrl+C stop · output is tailed",
        ),
        ("", "\n"),
    ]

    if snapshot.get("ok") is False:
        lines.append(("class:ps.error", f"  {snapshot.get('reason') or 'snapshot_failed'}"))
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


def render_exec_session_summary(
    application: ApplicationSink,
    snapshot: dict[str, typing.Any]
) -> None:
    """渲染 exec_command 查看面板的最终摘要。"""
    render_command_summary(application, exec_session_command_summary(snapshot))


def render_exec_session_detached(
    application: ApplicationSink,
    snapshot: dict[str, typing.Any],
) -> None:
    """渲染进程会话已转入后台的摘要。"""
    session_id = str(snapshot.get("session_id") or "").strip()
    render_command_summary(application, CommandSummary(
        kind=_session_kind(snapshot),
        command=str(snapshot.get("command") or session_id or "command"),
        suffix=f" · background · {session_id}",
        lines=(),
    ))


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
    max_lines: int = PS_SUMMARY_MAX_LINES
) -> list[str]:
    """返回 exec_command 摘要输出行。"""
    limit = max(1, int(max_lines or PS_SUMMARY_MAX_LINES))

    display = [
        _clip_inline(line, 160)
        for line in _panel_output_lines(snapshot, limit=limit)
        if str(line or "").strip()
    ]
    if display:
        return display[-limit:]

    exit_code = snapshot.get("exit_code")
    if exit_code not in (None, 0):
        return [
            f"{_session_kind(snapshot)} exited with code {int(exit_code or 0)}"
        ]
    return []


def _running_items(snapshot: typing.Any) -> list[dict[str, typing.Any]]:
    """从运行中会话快照提取可选项。"""
    if not isinstance(snapshot, dict):
        return []

    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []

    return [item for item in raw_items if isinstance(item, dict)]


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
    lines     = [str(line) for line in raw_lines] if isinstance(raw_lines, list) else []

    if not lines:
        output = str(snapshot.get("output") or "")
        lines = [line for line in output.splitlines() if line]

    return lines[-max(1, int(limit or 1)):]


def _ps_panel_height(terminal_height: int | None = None) -> int:
    """根据终端高度计算查看面板高度。"""
    height = terminal_height
    if not isinstance(height, int) or height <= 0:
        height = shutil.get_terminal_size(fallback=(100, 24)).lines
    return max(8, min(28, height - 4))


def _terminal_width(terminal_width: int | None = None) -> int:
    """返回当前终端宽度。"""
    if isinstance(terminal_width, int) and terminal_width > 0:
        return terminal_width
    return shutil.get_terminal_size(fallback=(100, 24)).columns


def _clip_inline(value: typing.Any, limit: int) -> str:
    """裁剪单行文本。"""
    text = " ".join(str(value or "").split())
    size = max(1, int(limit or 1))

    if len(text) <= size:
        return text
    if size <= 1:
        return "…"

    return f"{text[:size - 1]}…"


if __name__ == '__main__':
    pass
