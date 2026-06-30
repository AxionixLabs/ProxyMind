# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import shutil
import typing
import asyncio
from dataclasses import dataclass
from rich.text import Text
from mind_core.design import Design
from mind_app.stream_events.tool_trace import (
    render_tool_result_preview,
    render_tool_trace,
    render_tool_trace_parts
)
from mind_app.stream_events.tool_traces.shell_errors import (
    shell_error_diagnostic_lines,
    shell_output_lines
)
from mind_nova import const


@dataclass(frozen=True, slots=True)
class ShellEscape(object):
    """描述 REPL 中的本地 shell escape。"""
    enter_shell: bool
    command: str = ""


def parse_shell_escape(value: str) -> ShellEscape | None:
    """解析 REPL 输入中的 ! shell escape。"""
    text = str(value or "")
    if not text.startswith("!"):
        return None

    command = text[1:].strip()
    return ShellEscape(enter_shell=not command, command=command)


async def run_shell_escape(value: str) -> bool:
    """执行 REPL shell escape；返回是否已处理。"""
    parsed = parse_shell_escape(value)
    if parsed is None:
        return False

    if parsed.enter_shell:
        await run_interactive_shell()
        return True

    rc, stdout, stderr = await run_shell_command(parsed.command)
    render_shell_escape_summary(parsed.command, rc=rc, stdout=stdout, stderr=stderr)
    return True


async def run_interactive_shell() -> int:
    """进入当前平台默认 shell，直到用户 exit。"""
    executable = default_shell_executable()
    if not executable:
        Design.console.print("[bold #FF6B6B]Shell unavailable[/]")
        return 1

    process = await asyncio.create_subprocess_exec(executable)
    return await process.wait()


async def run_shell_command(command: str) -> tuple[int, str, str]:
    """执行一条本地 shell 命令并捕获输出。"""
    executable = default_shell_executable()
    if not executable:
        return 1, "", "Shell unavailable"

    args = shell_command_args(executable, command)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout_raw, stderr_raw = await process.communicate()

    return (
        int(process.returncode or 0),
        decode_process_output(stdout_raw),
        decode_process_output(stderr_raw)
    )


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


def decode_process_output(value: bytes) -> str:
    """按平台默认编码解码 shell 输出。"""
    if not value:
        return ""

    encodings = [
        sys.stdout.encoding,
        const.CHARSET,
        "mbcs" if os.name == "nt" else "",
        "gbk" if os.name == "nt" else "",
    ]

    for encoding in encodings:
        if not encoding:
            continue
        try:
            return value.decode(encoding, errors="replace")
        except LookupError:
            continue

    return value.decode(errors="replace")


def render_shell_escape_summary(
    command: str,
    *,
    rc: int,
    stdout: str,
    stderr: str
) -> None:
    """渲染 ! <cmd> 执行摘要。"""
    ok        = int(rc or 0) == 0
    arguments = {"command": str(command or "")}

    payload = {
        "ok"        : ok,
        "command"   : str(command or ""),
        "exit_code" : int(rc or 0),
        "stdout"    : stdout,
        "stderr"    : stderr
    }
    title = render_tool_trace(
        "shell_command",
        arguments,
        ok=ok,
        data=payload
    )

    preview = render_tool_result_preview("shell_command", payload, arguments=arguments)

    parts = render_tool_trace_parts(
        title,
        preview=preview,
        ok=ok,
        terminal_width=getattr(Design.console, "width", None)
    )
    Design.console.print(_parts_renderable(parts))


def shell_escape_summary(
    *,
    stdout: str,
    stderr: str,
    rc: int
) -> str:
    """返回 shell escape 的首条摘要。"""
    lines = shell_escape_summary_lines(stdout=stdout, stderr=stderr, rc=rc)
    return lines[0] if lines else ""


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


def _parts_renderable(parts: list[dict[str, typing.Optional[str]]]) -> Text:
    """把 trace parts 转成 Rich 文本对象。"""
    renderable = Text()
    for part in parts:
        part_text = str(part.get("text") or "")
        if part_text:
            renderable.append(part_text, style=part.get("style") or "bold")
    renderable.rstrip()
    return renderable


if __name__ == '__main__':
    pass
