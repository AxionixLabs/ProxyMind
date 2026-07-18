# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import time
import shlex
import signal
import shutil
import ctypes
import typing
import asyncio
import threading
import subprocess
from dataclasses import dataclass
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from mind_core.design import Design
from mind_core.terminal_input import clear_pending_input
from mind_app.modes.support.repl_summary import (
    CommandSummary,
    command_summary_title_parts,
    render_command_summary
)
from mind_app.stream_events.tool_traces.shell_errors import (
    shell_error_diagnostic_lines,
    shell_output_lines
)
from engine.encoding import decode_process_output

SHELL_COMMAND_TIMEOUT_SEC = 3600.0
SHELL_PANEL_DRAIN_SEC     = 0.25
SHELL_PANEL_TICK_SEC      = 0.25
SHELL_PANEL_BUFFER_CHARS  = 240000
SHELL_PANEL_MAX_LINES     = 600
SHELL_SUMMARY_MAX_LINES   = 8

SHELL_PANEL_STYLE = Style.from_dict({
    "shell.title.dot"     : "#7F8C9A",
    "shell.title.action"  : "bold #8FC7EA",
    "shell.title.command" : "bold #F4F7FA",
    "shell.title.suffix"  : "#7F8C9A",
    "shell.help"          : "#69727D",
    "shell.status"        : "#87919D",
    "shell.stdout"        : "#D8DCE2",
    "shell.stderr"        : "#B8C1CB",
})

SHELL_BUILTIN_HEADS = {
    "cat",
    "cd",
    "copy",
    "del",
    "dir",
    "echo",
    "erase",
    "ls",
    "move",
    "pwd",
    "rm",
    "set",
    "type",
    "where",
}

INTERACTIVE_SHELL_COMMANDS = frozenset({
    "ftp",
    "htop",
    "less",
    "more",
    "nano",
    "passwd",
    "sftp",
    "ssh",
    "telnet",
    "top",
    "vi",
    "vim",
})


@dataclass(frozen=True, slots=True)
class ShellEscape(object):
    """描述 REPL 中的本地 shell escape。"""
    enter_shell: bool
    command: str = ""


@dataclass(frozen=True, slots=True)
class ShellRunResult(object):
    """记录 REPL shell 命令结果。"""
    exit_code: int
    stdout: str
    stderr: str
    display_lines: tuple[str, ...] = ()
    elapsed_sec: float = 0.0
    timed_out: bool = False
    stopped: bool = False


class ShellPanelRun(object):
    """管理前台 shell 面板对应的本地进程。"""

    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        process: subprocess.Popen[bytes],
        timeout_sec: float
    ) -> None:
        """保存进程、输出缓冲和线程状态。"""
        self.command     = command
        self.args        = args
        self.process     = process
        self.timeout_sec = timeout_sec
        self.started_at  = time.monotonic()

        self.stdout_text = ""
        self.stderr_text = ""

        self.output_lines: list[tuple[str, str]] = []

        self.pending_lines = {
            "stdout": "",
            "stderr": "",
        }

        self.done_event = threading.Event()
        self.lock       = threading.RLock()

        self.exit_code: int | None = None

        self.timed_out: bool = False
        self.stopped: bool   = False
        self.closed: bool    = False

        self.stdout_thread = threading.Thread(
            target=self._read_stream,
            args=("stdout", process.stdout),
            daemon=True,
            name="mind-repl-shell-stdout"
        )
        self.stderr_thread = threading.Thread(
            target=self._read_stream,
            args=("stderr", process.stderr),
            daemon=True,
            name="mind-repl-shell-stderr"
        )
        self.wait_thread = threading.Thread(
            target=self._wait_process,
            daemon=True,
            name="mind-repl-shell-wait"
        )

    @classmethod
    def start(
        cls,
        *,
        command: str,
        timeout_sec: float = SHELL_COMMAND_TIMEOUT_SEC
    ) -> "ShellPanelRun | None":
        """启动 shell 进程并开始后台读取。"""
        executable = default_shell_executable()
        if not executable:
            return None

        args = direct_command_args(command) or shell_command_args(executable, command)
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_windows_creationflags(),
            start_new_session=os.name != "nt",
        )

        run = cls(
            command=command,
            args=args,
            process=process,
            timeout_sec=max(1.0, float(timeout_sec or SHELL_COMMAND_TIMEOUT_SEC))
        )
        run.stdout_thread.start()
        run.stderr_thread.start()
        run.wait_thread.start()
        return run

    def result(self) -> ShellRunResult:
        """返回当前结果快照。"""
        with self.lock:
            exit_code = self.exit_code
            if exit_code is None:
                exit_code = self.process.poll()
            if exit_code is None:
                exit_code = -1
            return ShellRunResult(
                exit_code=int(exit_code),
                stdout=self.stdout_text,
                stderr=self.stderr_text,
                display_lines=tuple(text for _, text in self._result_output_lines()),
                elapsed_sec=max(0.0, time.monotonic() - self.started_at),
                timed_out=self.timed_out,
                stopped=self.stopped
            )

    def live_lines(self, *, height: int) -> StyleAndTextTuples:
        """生成 shell 面板展示内容。"""
        with self.lock:
            body_height = max(1, height - 3)
            visible = list(self.output_lines[-body_height:])

        lines: StyleAndTextTuples = [
            *[
                (style, text)
                for text, style in shell_panel_title_fragments(
                    self.command,
                    suffix="",
                    terminal_width=shutil.get_terminal_size(fallback=(100, 24)).columns,
                    style_prefix="class:"
                )
            ],
            ("", "\n"),
            ("class:shell.help", "Ctrl+C/Esc/q stop · output is tailed"),
            ("", "\n"),
        ]

        if visible:
            for style, text in visible:
                lines.append(("class:shell.status", "  "))
                lines.append((style, text))
                lines.append(("", "\n"))
        else:
            lines.append(("class:shell.status", "(waiting for output)"))
            lines.append(("", "\n"))

        return lines

    def stop(self, *, force: bool = False) -> None:
        """停止 shell 进程树。"""
        if self.process.poll() is not None:
            return None

        with self.lock:
            self.stopped = True

        if not force:
            _interrupt_process_tree(self.process)
            if self._wait_briefly(0.8):
                return None

        _kill_process_tree(self.process)
        self._wait_briefly(1.0)

    def wait(self, timeout_sec: float | None = None) -> bool:
        """等待命令完成。"""
        return self.done_event.wait(timeout=timeout_sec)

    def close_streams(self) -> None:
        """关闭父进程持有的输出读取端。"""
        with self.lock:
            if self.closed:
                return None
            self.closed = True

        closer = threading.Thread(
            target=self._close_streams_blocking,
            daemon=True,
            name="mind-repl-shell-close"
        )
        closer.start()

    def _close_streams_blocking(self) -> None:
        """在后台关闭输出流。"""
        for stream in (self.process.stdout, self.process.stderr):
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except (OSError, RuntimeError, ValueError):
                    pass

    def mark_timed_out(self) -> None:
        """标记命令超时。"""
        with self.lock:
            self.timed_out = True

    def _wait_briefly(self, timeout_sec: float) -> bool:
        try:
            self.process.wait(timeout=max(0.01, timeout_sec))
            return True
        except subprocess.TimeoutExpired:
            return False

    def _wait_process(self) -> None:
        try:
            code = self.process.wait()
        except (OSError, RuntimeError, ValueError):
            code = self.process.poll()
            if code is None:
                code = -1
        with self.lock:
            self.exit_code = int(code)
        self.done_event.set()

    def _read_stream(self, name: str, stream: typing.BinaryIO | None) -> None:
        if stream is None:
            return None

        style = "class:shell.stdout" if name == "stdout" else "class:shell.stderr"

        while True:
            try:
                read_chunk = getattr(stream, "read1", None)
                chunk = read_chunk(4096) if callable(read_chunk) else stream.read(4096)
            except (OSError, RuntimeError, ValueError):
                return None
            if not chunk:
                return None

            text = decode_process_output(chunk)
            self._append_output(name, text, style)

    def _append_output(self, name: str, text: str, style: str) -> None:
        with self.lock:
            if name == "stdout":
                self.stdout_text = _tail_text(
                    self.stdout_text + text,
                    SHELL_PANEL_BUFFER_CHARS
                )
            else:
                self.stderr_text = _tail_text(
                    self.stderr_text + text,
                    SHELL_PANEL_BUFFER_CHARS
                )

            self._append_display_text(name, text, style)

    def _append_display_text(self, name: str, text: str, style: str) -> None:
        """把输出文本按完整行追加到展示缓冲。"""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        combined = self.pending_lines.get(name, "") + normalized

        if combined.endswith("\n"):
            complete_lines = combined.split("\n")[:-1]
            self.pending_lines[name] = ""
        else:
            parts = combined.split("\n")
            complete_lines = parts[:-1]
            self.pending_lines[name] = parts[-1] if parts else ""

        for line in complete_lines:
            if line:
                self.output_lines.append((style, _clip_inline(line, 240)))

        if len(self.output_lines) > SHELL_PANEL_MAX_LINES:
            del self.output_lines[:-SHELL_PANEL_MAX_LINES]

    def _result_output_lines(self) -> list[tuple[str, str]]:
        """返回包含未完成行的最终展示行。"""
        lines = list(self.output_lines)
        for name, style in (
            ("stdout", "class:shell.stdout"),
            ("stderr", "class:shell.stderr"),
        ):
            pending = self.pending_lines.get(name, "")
            if pending:
                lines.append((style, _clip_inline(pending, 240)))
        return lines[-SHELL_PANEL_MAX_LINES:]


async def run_shell_escape(value: str) -> bool:
    """执行 REPL shell escape；返回是否已处理。"""
    parsed = parse_shell_escape(value)
    if parsed is None:
        return False

    if parsed.enter_shell:
        await run_interactive_shell()
        return True

    if blocked_command := blocked_interactive_shell_command(parsed.command):
        render_blocked_interactive_shell_command(parsed.command, blocked_command)
        return True

    result = await run_shell_command_panel(parsed.command)
    render_shell_panel_summary(parsed.command, result)
    return True


async def run_interactive_shell() -> int:
    """进入当前平台默认 shell，直到用户 exit。"""
    executable = default_shell_executable()
    if not executable:
        Design.console.print("[bold #FF6B6B]Shell unavailable[/]")
        return 1

    try:
        process = await asyncio.create_subprocess_exec(executable)
    except (OSError, RuntimeError, ValueError) as exc:
        Design.console.print(f"[bold #FF6B6B]{exc}[/]")
        return 1
    return await process.wait()


async def run_shell_command_panel(
    command: str,
    *,
    show_panel: bool = True,
    timeout_sec: float = SHELL_COMMAND_TIMEOUT_SEC
) -> ShellRunResult:
    """运行 shell 命令并返回结果。"""
    try:
        run = ShellPanelRun.start(command=str(command or ""), timeout_sec=timeout_sec)
    except (OSError, RuntimeError, ValueError) as exc:
        message = str(exc) or exc.__class__.__name__
        return ShellRunResult(
            exit_code=1,
            stdout="",
            stderr=message,
            display_lines=(message,)
        )
    if run is None:
        return ShellRunResult(exit_code=1, stdout="", stderr="Shell unavailable")

    try:
        if show_panel and sys.stdin.isatty():
            return await _run_shell_panel(run)
        return await _run_shell_without_panel(run)
    except KeyboardInterrupt:
        run.stop(force=False)
        await _drain_and_close(run)
        return run.result()
    except asyncio.CancelledError:
        run.stop(force=True)
        await _drain_and_close(run)
        raise


async def _run_shell_without_panel(run: ShellPanelRun) -> ShellRunResult:
    """无交互环境下等待 shell 命令完成。"""
    completed = await asyncio.to_thread(run.wait, run.timeout_sec)
    if not completed:
        run.mark_timed_out()
        run.stop(force=True)
    await _drain_and_close(run)
    return run.result()


async def _run_shell_panel(run: ShellPanelRun) -> ShellRunResult:
    """显示前台 shell 输出面板。"""
    height   = _shell_panel_height()
    bindings = KeyBindings()

    stop_tasks: list[asyncio.Task[None]]  = []
    watch_tasks: list[asyncio.Task[None]] = []
    tick_tasks: list[asyncio.Task[None]]  = []

    async def stop(app_shell: Application[ShellRunResult]) -> None:
        run.stop(force=False)
        await _drain_and_close(run)
        if not app_shell.is_done:
            app_shell.exit(result=run.result())

    def request_stop(event) -> None:
        if not stop_tasks:
            stop_tasks.append(asyncio.create_task(stop(event.app)))

    @bindings.add("c-c", eager=True)
    @bindings.add("escape", eager=True)
    @bindings.add("q", eager=True)
    def _(event) -> None:
        request_stop(event)

    control = FormattedTextControl(
        lambda: run.live_lines(height=height),
        focusable=True
    )

    app: Application[ShellRunResult] = Application(
        layout=Layout(
            Window(
                content=control,
                height=height,
                always_hide_cursor=True
            ),
            focused_element=control
        ),
        key_bindings=bindings,
        style=SHELL_PANEL_STYLE,
        full_screen=False,
        erase_when_done=True,
        mouse_support=False
    )

    async def watch() -> None:
        completed = await asyncio.to_thread(run.wait, run.timeout_sec)
        if not completed:
            run.mark_timed_out()
            run.stop(force=True)
        await _drain_and_close(run)
        if not app.is_done:
            app.exit(result=run.result())

    async def tick() -> None:
        while not app.is_done and not run.done_event.is_set():
            app.invalidate()
            await asyncio.sleep(SHELL_PANEL_TICK_SEC)
        if not app.is_done:
            app.invalidate()

    def pre_run() -> None:
        clear_pending_input(app.input)
        watch_tasks.append(asyncio.create_task(watch()))
        tick_tasks.append(asyncio.create_task(tick()))

    try:
        return await app.run_async(pre_run=pre_run)
    finally:
        for task in [*watch_tasks, *stop_tasks, *tick_tasks]:
            if not task.done():
                task.cancel()
        if watch_tasks or stop_tasks or tick_tasks:
            await asyncio.gather(*watch_tasks, *stop_tasks, *tick_tasks, return_exceptions=True)
        if run.process.poll() is None:
            run.stop(force=True)
            await _drain_and_close(run)


async def _drain_and_close(run: ShellPanelRun) -> None:
    """短暂等待输出读取后关闭输出管道。"""
    await asyncio.sleep(SHELL_PANEL_DRAIN_SEC)
    run.close_streams()


def parse_shell_escape(value: str) -> ShellEscape | None:
    """解析 REPL 输入中的 ! shell escape。"""
    text = str(value or "")
    if not text.startswith("!"):
        return None

    command = text[1:].strip()
    return ShellEscape(enter_shell=not command, command=command)


def default_shell_executable() -> str:
    """返回当前平台默认 shell。"""
    if os.name == "nt":
        return (
            shutil.which("pwsh")
            or shutil.which("powershell")
            or shutil.which("cmd")
            or ""
        )

    configured = os.environ.get("SHELL")
    if configured and shutil.which(configured):
        return configured

    return shutil.which("bash") or shutil.which("sh") or ""


def shell_command_args(executable: str, command: str) -> list[str]:
    """根据 shell 类型生成执行单条命令的参数。"""
    name = os.path.basename(str(executable or "")).lower()
    if name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
        return [executable, "-NoLogo", "-NoProfile", "-Command", command]
    if name in {"cmd", "cmd.exe"}:
        return [executable, "/d", "/s", "/c", command]
    return [executable, "-lc", command]


def direct_command_args(command: str) -> list[str] | None:
    """返回可绕过 shell wrapper 的外部命令参数。"""
    text = str(command or "").strip()
    if not text or _contains_shell_syntax(text):
        return None

    parts = _split_direct_command(text)
    if not parts:
        return None

    head = parts[0]
    if os.path.basename(head).lower() in SHELL_BUILTIN_HEADS:
        return None

    resolved = shutil.which(head)
    if not resolved and os.path.exists(head):
        resolved = head
    if not resolved:
        return None

    suffix = os.path.splitext(resolved)[1].lower()
    if suffix in {".bat", ".cmd"}:
        return None

    return [resolved, *parts[1:]]


def render_shell_panel_summary(command: str, result: ShellRunResult) -> None:
    """渲染 REPL shell 前台面板的最终摘要。"""
    render_command_summary(shell_panel_command_summary(command, result))


def shell_panel_command_summary(
    command: str,
    result: ShellRunResult
) -> CommandSummary:
    """把 shell 面板结果转换为统一命令摘要。"""
    return CommandSummary(
        kind="Shell",
        command=command,
        suffix=shell_panel_status_suffix(result),
        lines=tuple(shell_panel_summary_lines(result))
    )


def shell_panel_summary_title_parts(
    command: str,
    result: ShellRunResult,
    *,
    terminal_width: int | None = None
) -> list[tuple[str, str]]:
    """返回 shell 摘要标题的分段样式。"""
    return command_summary_title_parts(
        shell_panel_command_summary(command, result),
        terminal_width=terminal_width
    )


def shell_panel_title_fragments(
    command: str,
    *,
    suffix: str = "",
    terminal_width: int | None = None,
    style_prefix: str = ""
) -> list[tuple[str, str]]:
    """返回 shell 标题分段。"""
    command_text = shell_panel_summary_command_text(
        command,
        suffix=suffix,
        terminal_width=terminal_width
    )

    prefix = str(style_prefix or "")

    parts = [
        ("• ", f"{prefix}shell.title.dot"),
        ("Shell", f"{prefix}shell.title.action"),
        (" ", f"{prefix}shell.title.dot"),
        (command_text, f"{prefix}shell.title.command"),
    ]

    if suffix:
        parts.append((suffix, f"{prefix}shell.title.suffix"))

    return parts


def shell_panel_summary_command_text(
    command: str,
    *,
    suffix: str = "",
    terminal_width: int | None = None
) -> str:
    """返回适合单行标题展示的命令文本。"""
    text = str(command or "shell command").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        text = "shell command"

    width        = int(terminal_width or 100)
    prefix_width = len("• Shell ")
    available    = max(12, width - prefix_width - len(str(suffix or "")))

    return _clip_inline(text, available)


def shell_panel_status_suffix(
    result: ShellRunResult
) -> str:
    """返回 shell 摘要标题状态后缀。"""
    if result.timed_out:
        return " · timeout"
    if result.stopped:
        return " · stop"
    if int(result.exit_code or 0) != 0:
        return f" · exit {int(result.exit_code or 0)}"

    return ""


def shell_panel_summary_lines(
    result: ShellRunResult,
    *,
    max_lines: int = SHELL_SUMMARY_MAX_LINES
) -> list[str]:
    """返回按面板接收顺序排列的摘要行。"""
    limit = max(1, int(max_lines or SHELL_SUMMARY_MAX_LINES))

    display = [
        _short_line(line)
        for line in result.display_lines
        if str(line or "").strip()
    ]

    if display:
        return display[-limit:]

    fallback = shell_escape_summary_lines(
        stdout=result.stdout,
        stderr=result.stderr,
        rc=result.exit_code
    )

    if fallback == ["(no output)"] and int(result.exit_code or 0) == 0:
        return []
    return fallback[:limit]


def blocked_interactive_shell_command(command: str) -> str:
    """返回需要屏蔽的交互命令名称。"""
    parts = _split_command_parts(command)
    if not parts:
        return ""

    name = _command_name(parts[0])
    if name in INTERACTIVE_SHELL_COMMANDS:
        return name

    return ""


def render_blocked_interactive_shell_command(command: str, name: str) -> None:
    """渲染交互命令被屏蔽的提示。"""
    render_command_summary(CommandSummary(
        kind="Shell",
        command=command,
        suffix=" · blocked",
        lines=(
            f"Interactive command blocked: {name}",
            "Run it in a terminal outside.",
        )
    ))


def shell_escape_summary_lines(
    *,
    stdout: str,
    stderr: str,
    rc: int
) -> list[str]:
    """返回 shell escape 的摘要行。"""
    if int(rc or 0) != 0:
        source = stderr or stdout
        diagnostic = shell_error_diagnostic_lines(
            _output_lines(source),
            max_context_lines=4
        )
        if diagnostic:
            return diagnostic

        lines = _non_empty_output_lines(source)
        if lines:
            return lines[:4]
        return [f"Shell exited with code {int(rc or 0)}"]

    lines = _non_empty_output_lines(stdout or stderr)
    if lines:
        return [_short_line(lines[0])]
    return ["(no output)"]


def _split_direct_command(command: str) -> list[str]:
    """按平台规则拆分简单外部命令。"""
    return _split_command_parts(command)


def _split_command_parts(command: str) -> list[str]:
    """按平台规则拆分命令文本。"""
    text = str(command or "").strip()
    if not text:
        return []

    if os.name == "nt":
        return _windows_command_line_to_argv(text)

    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return []


def _command_name(value: str) -> str:
    """返回命令名的小写规范形式。"""
    text = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    stem, suffix = os.path.splitext(text)
    if suffix in {".bat", ".cmd", ".com", ".exe"}:
        return stem
    return text


def _windows_command_line_to_argv(command: str) -> list[str]:
    """使用 Windows API 拆分命令行。"""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return []

    shell32 = getattr(windll, "shell32", None)
    kernel32 = getattr(windll, "kernel32", None)

    if shell32 is None or kernel32 is None:
        return []

    command_line_to_argv: typing.Any = getattr(shell32, "CommandLineToArgvW", None)
    local_free: typing.Any = getattr(kernel32, "LocalFree", None)

    if not callable(command_line_to_argv) or not callable(local_free):
        return []

    command_line_to_argv.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_int)
    ]
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)

    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    argc = ctypes.c_int(0)

    argv: typing.Any = command_line_to_argv(command, ctypes.byref(argc))
    if not argv:
        return []

    try:
        parts: list[str] = []
        for index in range(argc.value):
            part = str(argv[index] or "")
            if index > 0 or part.strip():
                parts.append(part)
        return parts
    finally:
        local_free(argv)


def _contains_shell_syntax(command: str) -> bool:
    """判断命令是否需要 shell 解释。"""
    text = str(command or "")
    return any(marker in text for marker in (
        "|", ">", "<", "&", ";", "`", "$", "%", "*", "?", "\n", "\r"
    ))


def _windows_creationflags() -> int:
    """返回 Windows 子进程组创建标志。"""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0)


def _interrupt_process_tree(process: subprocess.Popen[bytes]) -> None:
    """向进程树发送中断信号。"""
    if process.poll() is not None:
        return None

    if os.name == "nt":
        break_signal = getattr(signal, "CTRL_BREAK_EVENT", None)
        if break_signal is None:
            return None
        try:
            process.send_signal(break_signal)
        except (ProcessLookupError, RuntimeError, ValueError, OSError):
            return None

        return None

    try:
        os.killpg(process.pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError, RuntimeError, ValueError, OSError):
        try:
            process.terminate()
        except (ProcessLookupError, RuntimeError, ValueError):
            return None


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """强制终止进程树。"""
    if process.poll() is not None:
        return None

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False
            )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except (ProcessLookupError, RuntimeError, ValueError, OSError):
                pass

        return None

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, RuntimeError, ValueError, OSError):
        try:
            process.kill()
        except (ProcessLookupError, RuntimeError, ValueError):
            return None


def _shell_panel_height() -> int:
    """返回 shell 面板高度。"""
    terminal = shutil.get_terminal_size(fallback=(100, 24))
    return max(8, min(20, terminal.lines - 4))


def _tail_text(value: str, limit: int) -> str:
    """保留文本尾部。"""
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


def _clip_inline(value: typing.Any, limit: int) -> str:
    """裁剪单行文本。"""
    text = str(value or "").replace("\t", "    ").rstrip()
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


def _output_lines(value: typing.Any) -> list[str]:
    return shell_output_lines(value, keep_empty=True)


def _non_empty_output_lines(value: typing.Any) -> list[str]:
    return [
        _short_line(line)
        for line in _output_lines(value)
        if str(line or "").strip()
    ]


def _short_line(value: typing.Any, limit: int = 160) -> str:
    text = str(value or "").rstrip()
    if len(text) <= limit:
        return text
    return f"{text[:max(0, limit - 3)]}..."


if __name__ == '__main__':
    pass
