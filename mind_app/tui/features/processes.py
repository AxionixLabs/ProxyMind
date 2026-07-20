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
from .summary import (
    CommandSummary,
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

if typing.TYPE_CHECKING:
    from ..core.runtime import TuiRuntime


async def choose_exec_session(
    runtime: "TuiRuntime",
    mind: typing.Any,
) -> str | None:
    """在主 TUI 中选择一个运行中的命令会话。"""
    application = mind.frontend.application
    snapshot = await mind.native_coding.running_exec_sessions()
    sessions = _running_items(snapshot)

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
        title="Exec Commands",
        status=f"running={len(sessions)}",
        options=tuple(
            MenuOption(
                value=str(item.get("session_id") or "").strip() or None,
                label=_clip_inline(item.get("command"), 80),
                detail=f"pid={item.get('pid') or '-'}",
            )
            for item in sessions
        ),
    ))


async def watch_exec_session(
    runtime: "TuiRuntime",
    mind: typing.Any,
    session_id: str | None,
) -> bool:
    """在主 TUI 中持续查看命令会话输出。"""
    sid = str(session_id or "").strip()
    if not sid:
        return False

    application = mind.frontend.application
    height = _ps_panel_height(application.viewport.height)

    state: dict[str, typing.Any] = {
        "snapshot": {
            "ok"         : True,
            "session_id" : sid,
            "status"     : "running",
            "output_lines": [],
        },
        "last_snapshot": None,
        "updated_at": time.time(),
    }
    return await _watch_exec_session(
        mind,
        sid,
        state,
        runtime=runtime,
        height=height,
    )


async def _watch_exec_session(
    mind: typing.Any,
    session_id: str,
    state: dict[str, typing.Any],
    *,
    runtime: "TuiRuntime",
    height: int,
) -> bool:
    """轮询并更新主 TUI 中的命令会话面板。"""
    application = mind.frontend.application

    def request() -> MenuRequest:
        fragments = render_exec_session_panel(
            state,
            height=max(4, height - 1),
            terminal_width=application.viewport.width,
        )
        text = "".join(value for _style, value in fragments)
        return MenuRequest(
            title="Exec Command",
            status=session_id,
            body=tuple(text.rstrip().splitlines()),
            help_text="Esc/q close · read-only · output is tailed",
        )

    viewer_task = asyncio.create_task(runtime.select_menu(request()))

    async def poll() -> None:
        while not viewer_task.done():
            snapshot = await mind.native_coding.exec_session_output_snapshot(
                session_id=session_id,
                max_output_chars=PS_OUTPUT_LIMIT,
            )
            if snapshot.get("ok") is False:
                runtime.finish_menu(state.get("last_snapshot"))
                return None

            state["snapshot"] = snapshot
            state["updated_at"] = time.time()
            state["last_snapshot"] = snapshot
            runtime.update_menu(request())

            if str(snapshot.get("status") or "").strip() == "exited":
                runtime.finish_menu(snapshot)
                return None
            await asyncio.sleep(PS_PANEL_TICK_SEC)

    poll_task = asyncio.create_task(poll())
    try:
        result = await viewer_task
    finally:
        if not poll_task.done():
            poll_task.cancel()
        await asyncio.gather(poll_task, return_exceptions=True)

    if isinstance(result, dict):
        render_exec_session_summary(application, result)
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
        ("class:ps.title", "Exec Commands"),
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
        ("class:ps.help", "Ctrl+C/Esc/q close · read-only · output is tailed"),
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


def exec_session_command_summary(snapshot: dict[str, typing.Any]) -> CommandSummary:
    """把 exec_command 快照转换为统一命令摘要。"""
    return CommandSummary(
        kind="Exec",
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
        return [f"exec_command exited with code {int(exit_code or 0)}"]
    return []


def _running_items(snapshot: typing.Any) -> list[dict[str, typing.Any]]:
    """从运行中会话快照提取可选项。"""
    if not isinstance(snapshot, dict):
        return []

    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []

    return [item for item in raw_items if isinstance(item, dict)]


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

    command = _clip_inline(snapshot.get("command"), max(12, terminal_width - 32))
    if command:
        return f"Exec {status} · {sid} · {command}"

    return f"Exec {status} · {sid}"


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
