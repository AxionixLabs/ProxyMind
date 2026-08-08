# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import shlex
import shutil
import ctypes
import typing
import asyncio
from mind_app.frontend import (
    ApplicationSink,
    ApplicationView
)
from ..core.styles import failure_text_block
from .processes import watch_exec_session
from .summary import (
    CommandSummary,
    render_command_summary
)

SHELL_COMMAND_TIMEOUT_SEC = 3600

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


async def run_shell_escape(
    runtime: typing.Any,
    mind: typing.Any,
    value: str
) -> bool:
    """执行 TUI shell escape 并管理进程查看状态。"""
    command = parse_shell_escape(value)
    if command is None:
        return False
    if not command:
        return True

    application = mind.frontend.application

    blocked = blocked_interactive_shell_command(command)
    if blocked:
        render_blocked_interactive_shell_command(
            application,
            command,
            blocked,
        )
        return True

    executable = default_shell_executable()
    if not executable:
        application.emit(ApplicationView(
            type="tui.shell.unavailable",
            renderable=failure_text_block("Shell unavailable"),
        ))
        return True

    args = direct_command_args(command)
    if args is None:
        args = shell_command_args(executable, command)

    try:
        snapshot = await mind.native_coding.start_user_shell_session(
            command=command,
            args=args,
            timeout_sec=SHELL_COMMAND_TIMEOUT_SEC,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        render_shell_start_failure(application, command, exc)
        return True

    if snapshot.get("ok") is False:
        render_shell_start_failure(
            application,
            command,
            snapshot.get("reason") or "shell_start_failed",
        )
        return True

    session_id = str(snapshot.get("session_id") or "").strip()
    viewer_ready = asyncio.Event()
    runtime.start_background_task(
        watch_exec_session(
            runtime,
            mind,
            session_id,
            announce_detach=True,
            initial_snapshot=snapshot,
            viewer_mode="inline",
            ready_event=viewer_ready,
        ),
        name=f"shell viewer {session_id}",
    )
    await viewer_ready.wait()
    return True


def parse_shell_escape(value: str) -> str | None:
    """解析 TUI 输入中的 shell escape。"""
    text = str(value or "")
    if not text.startswith("!"):
        return None
    return text[1:].strip()


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
    parts = _split_command_parts(text)
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
    if os.path.splitext(resolved)[1].lower() in {".bat", ".cmd"}:
        return None

    return [resolved, *parts[1:]]


def blocked_interactive_shell_command(command: str) -> str:
    """返回需要屏蔽的交互命令名称。"""
    parts = _split_command_parts(command)
    if not parts:
        return ""
    name = _command_name(parts[0])
    return name if name in INTERACTIVE_SHELL_COMMANDS else ""


def render_blocked_interactive_shell_command(
    application: ApplicationSink,
    command: str,
    name: str
) -> None:
    """渲染交互命令被屏蔽的提示。"""
    render_command_summary(
        application,
        CommandSummary(
            kind="Shell",
            command=command,
            suffix=" · blocked",
            lines=(f"Interactive command blocked: {name}",),
        ),
        first_line_prefix="└ ",
    )


def render_shell_start_failure(
    application: ApplicationSink,
    command: str,
    error: typing.Any
) -> None:
    """渲染 shell 会话启动失败摘要。"""
    render_command_summary(
        application,
        CommandSummary(
            kind="Shell",
            command=command,
            suffix=" · failed",
            lines=(str(error or "shell_start_failed"),),
        ),
        first_line_prefix="└ ",
    )


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

    return stem if suffix in {".bat", ".cmd", ".com", ".exe"} else text


def _windows_command_line_to_argv(command: str) -> list[str]:
    """使用 Windows API 拆分命令行。"""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return []

    shell32  = getattr(windll, "shell32", None)
    kernel32 = getattr(windll, "kernel32", None)

    if shell32 is None or kernel32 is None:
        return []

    parse: typing.Any = getattr(shell32, "CommandLineToArgvW", None)
    free: typing.Any  = getattr(kernel32, "LocalFree", None)

    if not callable(parse) or not callable(free):
        return []

    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype  = ctypes.POINTER(ctypes.c_wchar_p)
    free.argtypes  = [ctypes.c_void_p]
    free.restype   = ctypes.c_void_p

    count = ctypes.c_int(0)

    argv: typing.Any = parse(command, ctypes.byref(count))

    if not argv:
        return []

    try:
        return [str(argv[index] or "") for index in range(count.value)]
    finally:
        free(argv)


def _contains_shell_syntax(command: str) -> bool:
    """判断命令是否需要 shell 解释。"""
    return any(marker in str(command or "") for marker in (
        "|", ">", "<", "&", ";", "`", "$", "%", "*", "?", "\n", "\r"
    ))


if __name__ == '__main__':
    pass
