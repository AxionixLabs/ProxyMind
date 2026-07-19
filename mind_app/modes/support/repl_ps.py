# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import shutil
import typing
import asyncio
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from mind_core.terminal_input import clear_pending_input
from mind_app.modes.support.repl_summary import (
    CommandSummary,
    command_summary_title_parts,
    render_command_summary
)

PS_PANEL_TICK_SEC: float   = 0.12
PS_OUTPUT_LIMIT: int       = 60000
PS_SUMMARY_MAX_LINES: int  = 8
PS_MENU_VISIBLE_LIMIT: int = 8

PS_MENU_STYLE = Style.from_dict({
    "ps.title"        : "bold #DCE6EE",
    "ps.help"         : "#69727D",
    "ps.status"       : "#87919D",
    "ps.index"        : "bold #8A949F",
    "ps.index.active" : "bold #F4F7FA bg:#3A4651",
    "ps.pid"          : "#7F8C9A",
    "ps.command"      : "bold #F4F7FA",
    "ps.active"       : "#F4F7FA bg:#26313A",
})

PS_PANEL_STYLE = Style.from_dict({
    "ps.title"   : "bold #8FC7EA",
    "ps.meta"    : "#87919D",
    "ps.help"    : "#69727D",
    "ps.output"  : "#F4F7FA",
    "ps.waiting" : "#7F8C9A",
    "ps.error"   : "bold #FF6B6B",
})


async def choose_exec_session(mind: typing.Any) -> str | None:
    """显示运行中 exec_command 会话菜单，并返回选中的会话 ID。"""
    application = mind.frontend.application
    snapshot = await mind.native_coding.running_exec_sessions()
    sessions = _running_items(snapshot)

    if not sessions:
        application.emit(ApplicationView(
            type="repl.exec.empty",
            renderable="[bold #7F8C9A]No running exec_command sessions.[/]",
        ))
        application.emit(ApplicationView(type="repl.gap"))
        return None

    selected = [0]
    bindings = KeyBindings()

    def move(step: int) -> None:
        selected[0] = min(len(sessions) - 1, max(0, selected[0] + step))

    def choose(index: int, event: typing.Any) -> None:
        if 0 <= index < len(sessions):
            session_id = str(sessions[index].get("session_id") or "").strip()
            event.app.exit(result=session_id or None)

    @bindings.add("enter")
    def _(event) -> None:
        choose(selected[0], event)

    @bindings.add("down")
    @bindings.add("c-n")
    def _(event) -> None:
        move(1)
        event.app.invalidate()

    @bindings.add("up")
    @bindings.add("c-p")
    def _(event) -> None:
        move(-1)
        event.app.invalidate()

    for number in range(1, min(9, PS_MENU_VISIBLE_LIMIT, len(sessions)) + 1):
        @bindings.add(str(number))
        def _(event, selected_number=number) -> None:
            start, visible = _visible_session_window(sessions, selected[0])
            if selected_number <= len(visible):
                choose(start + selected_number - 1, event)

    @bindings.add("c-c")
    @bindings.add("q")
    def _(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(
        lambda: render_exec_session_menu(
            sessions,
            selected[0],
            terminal_width=application.viewport.width,
        ),
        focusable=True
    )

    app: Application[str | None] = Application(
        layout=Layout(
            Window(
                content=control,
                height=min(len(sessions), PS_MENU_VISIBLE_LIMIT) + 4,
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=PS_MENU_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    return await app.run_async(pre_run=lambda: clear_pending_input(app.input))


async def watch_exec_session(mind: typing.Any, session_id: str | None) -> bool:
    """打开 exec_command 会话只读流式查看面板。"""
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
    bindings = KeyBindings()

    poll_tasks: list[asyncio.Task[None]] = []

    @bindings.add("c-c", eager=True)
    @bindings.add("escape", eager=True)
    @bindings.add("q", eager=True)
    def _(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(
        lambda: render_exec_session_panel(
            state,
            height=height,
            terminal_width=application.viewport.width,
        ),
        focusable=True
    )

    app: Application[dict[str, typing.Any] | None] = Application(
        layout=Layout(
            Window(
                content=control,
                height=height,
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=PS_PANEL_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    async def poll() -> None:
        while not app.is_done:
            snapshot = await mind.native_coding.exec_session_output_snapshot(
                session_id=sid,
                max_output_chars=PS_OUTPUT_LIMIT
            )
            if snapshot.get("ok") is False:
                last_snapshot = state.get("last_snapshot")
                if isinstance(last_snapshot, dict):
                    if not app.is_done:
                        app.exit(result=last_snapshot)
                    return None

            state["snapshot"]   = snapshot
            state["updated_at"] = time.time()

            if snapshot.get("ok") is True:
                state["last_snapshot"] = snapshot

            app.invalidate()
            if str(snapshot.get("status") or "").strip() == "exited":
                if not app.is_done:
                    app.exit(result=snapshot)
                return None

            await asyncio.sleep(PS_PANEL_TICK_SEC)

    def pre_run() -> None:
        clear_pending_input(app.input)
        poll_tasks.append(asyncio.create_task(poll()))

    result: dict[str, typing.Any] | None = None
    try:
        result = await app.run_async(pre_run=pre_run)
    finally:
        for task in poll_tasks:
            if not task.done():
                task.cancel()
        if poll_tasks:
            await asyncio.gather(*poll_tasks, return_exceptions=True)

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
