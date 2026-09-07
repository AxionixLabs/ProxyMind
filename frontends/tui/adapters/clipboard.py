# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import base64
import os
import platform
import shutil
import sys
import typing
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
)
from dataclasses import dataclass

from metadata import const


OSC52_MAX_RAW_BYTES: typing.Final[int] = 100_000

ClipboardCopy = Callable[[str], Awaitable[None]]
ClipboardCommand: typing.TypeAlias = tuple[str, ...]


class ClipboardError(RuntimeError):
    """剪贴板写入失败。"""


@dataclass(frozen=True, slots=True)
class ClipboardEnvironment:
    """描述一次剪贴板选择使用的稳定终端环境。"""

    platform_name: str
    ssh_session: bool
    tmux_session: bool
    wsl_session: bool


async def copy_text_to_clipboard(text: str) -> None:
    """按本机、WSL 和终端回退顺序把文本写入用户剪贴板。"""
    value = str(text or "")
    if not value:
        raise ClipboardError("clipboard text is empty")

    environment = clipboard_environment(
        os.environ,
        platform_name=sys.platform,
        release=platform.release(),
    )

    async def native_copy(content: str) -> None:
        await _native_clipboard_copy(
            content,
            platform_name=environment.platform_name,
        )

    async def terminal_copy(content: str) -> None:
        await _terminal_clipboard_copy(
            content,
            tmux_session=environment.tmux_session,
        )

    await _copy_text_to_clipboard_with(
        value,
        environment=environment,
        native_copy=native_copy,
        wsl_copy=_powershell_clipboard_copy,
        terminal_copy=terminal_copy,
    )


def clipboard_environment(
    environment: Mapping[str, str],
    *,
    platform_name: str,
    release: str,
) -> ClipboardEnvironment:
    """从显式平台输入构造不可变剪贴板环境。"""
    return ClipboardEnvironment(
        platform_name=str(platform_name),
        ssh_session=(
            "SSH_TTY" in environment
            or "SSH_CONNECTION" in environment
        ),
        tmux_session=(
            "TMUX" in environment
            or "TMUX_PANE" in environment
        ),
        wsl_session=(
            str(platform_name).startswith("linux")
            and (
                "WSL_DISTRO_NAME" in environment
                or "WSL_INTEROP" in environment
                or "microsoft" in str(release).casefold()
            )
        ),
    )


async def _copy_text_to_clipboard_with(
    text: str,
    *,
    environment: ClipboardEnvironment,
    native_copy: ClipboardCopy,
    wsl_copy: ClipboardCopy,
    terminal_copy: ClipboardCopy,
) -> None:
    """执行 Codex 对齐的剪贴板后端选择并保留完整错误原因。"""
    if environment.ssh_session:
        try:
            await terminal_copy(text)
        except ClipboardError as error:
            label = (
                "terminal clipboard copy failed over SSH"
                if environment.tmux_session
                else "OSC 52 clipboard copy failed over SSH"
            )
            raise ClipboardError(f"{label}: {error}") from error
        return None

    try:
        await native_copy(text)
        return None
    except ClipboardError as native_error:
        if environment.wsl_session:
            try:
                await wsl_copy(text)
                return None
            except ClipboardError as wsl_error:
                try:
                    await terminal_copy(text)
                    return None
                except ClipboardError as terminal_error:
                    terminal_label = (
                        "terminal fallback"
                        if environment.tmux_session
                        else "OSC 52 fallback"
                    )
                    raise ClipboardError(
                        f"native clipboard: {native_error}; "
                        f"WSL fallback: {wsl_error}; "
                        f"{terminal_label}: {terminal_error}"
                    ) from terminal_error

        try:
            await terminal_copy(text)
        except ClipboardError as terminal_error:
            terminal_label = (
                "terminal fallback"
                if environment.tmux_session
                else "OSC 52 fallback"
            )
            raise ClipboardError(
                f"native clipboard: {native_error}; "
                f"{terminal_label}: {terminal_error}"
            ) from terminal_error


async def _native_clipboard_copy(text: str, *, platform_name: str) -> None:
    """尝试当前平台的原生剪贴板命令。"""
    commands = _native_clipboard_commands(platform_name)
    if not commands:
        raise ClipboardError("clipboard command unavailable")

    failures: list[str] = []
    for command in commands:
        try:
            await _run_clipboard_command(command, text)
            return None
        except ClipboardError as error:
            failures.append(f"{command[0]}: {error}")
    raise ClipboardError("; ".join(failures))


def _native_clipboard_commands(platform_name: str) -> tuple[ClipboardCommand, ...]:
    """返回当前平台按优先级排列的可用原生剪贴板命令。"""
    if platform_name == "win32":
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        return (_powershell_command(executable),) if executable else ()

    if platform_name == "darwin":
        executable = shutil.which("pbcopy")
        return ((executable,),) if executable else ()

    commands: list[ClipboardCommand] = []
    for name, arguments in (
        ("wl-copy", ()),
        ("xclip", ("-selection", "clipboard")),
        ("xsel", ("--clipboard", "--input")),
    ):
        executable = shutil.which(name)
        if executable:
            commands.append((executable, *arguments))
    return tuple(commands)


async def _powershell_clipboard_copy(text: str) -> None:
    """通过 Windows PowerShell 写入剪贴板。"""
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if not executable:
        raise ClipboardError("powershell.exe unavailable")
    await _run_clipboard_command(_powershell_command(executable), text)


def _powershell_command(executable: str) -> ClipboardCommand:
    """构造使用 UTF-8 标准输入的 PowerShell 剪贴板命令。"""
    return (
        executable,
        "-NoProfile",
        "-Command",
        (
            "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
            "$ErrorActionPreference = 'Stop'; "
            "$text = [Console]::In.ReadToEnd(); "
            "Set-Clipboard -Value $text"
        ),
    )


async def _terminal_clipboard_copy(text: str, *, tmux_session: bool) -> None:
    """优先通过 tmux，再通过 OSC52 写入外层终端剪贴板。"""
    if tmux_session:
        try:
            await _tmux_clipboard_copy(text)
            return None
        except ClipboardError as tmux_error:
            try:
                await _osc52_clipboard_copy(text, tmux_session=True)
                return None
            except ClipboardError as osc52_error:
                raise ClipboardError(
                    f"tmux clipboard: {tmux_error}; "
                    f"OSC 52 fallback: {osc52_error}"
                ) from osc52_error
    await _osc52_clipboard_copy(text, tmux_session=False)


async def _tmux_clipboard_copy(text: str) -> None:
    """校验 tmux 转发能力并写入其剪贴板缓冲区。"""
    executable = shutil.which("tmux")
    if not executable:
        raise ClipboardError("tmux unavailable")
    setting = await _run_clipboard_command(
        (executable, "show-options", "-gv", "set-clipboard"),
        "",
    )
    if setting.strip() == "off":
        raise ClipboardError("tmux clipboard forwarding is disabled")
    info = await _run_clipboard_command((executable, "info"), "")
    if any("Ms: [missing]" in line for line in info.splitlines()):
        raise ClipboardError(
            "tmux clipboard forwarding is unavailable: missing Ms capability"
        )
    await _run_clipboard_command(
        (executable, "load-buffer", "-w", "-"),
        text,
    )


async def _osc52_clipboard_copy(text: str, *, tmux_session: bool) -> None:
    """通过当前控制终端发送 OSC52 剪贴板序列。"""
    sequence = osc52_sequence(text, tmux_session=tmux_session)
    try:
        await asyncio.to_thread(_write_terminal_sequence, sequence)
    except OSError as error:
        raise ClipboardError(
            f"failed to write OSC 52: {type(error).__name__}: {error}"
        ) from error


def osc52_sequence(text: str, *, tmux_session: bool) -> str:
    """编码受大小限制的 OSC52 序列。"""
    raw = str(text).encode(const.CHARSET)
    if len(raw) > OSC52_MAX_RAW_BYTES:
        raise ClipboardError(
            f"OSC 52 payload too large ({len(raw)} bytes; "
            f"max {OSC52_MAX_RAW_BYTES})"
        )
    encoded = base64.b64encode(raw).decode("ascii")
    if tmux_session:
        return f"\x1bPtmux;\x1b\x1b]52;c;{encoded}\x07\x1b\\"
    return f"\x1b]52;c;{encoded}\x07"


def _write_terminal_sequence(sequence: str) -> None:
    """把终端控制序列优先写入控制终端。"""
    if os.name != "nt":
        try:
            with open("/dev/tty", "w", encoding="ascii", newline="") as tty:
                tty.write(sequence)
                tty.flush()
                return None
        except OSError:
            pass
    sys.stdout.write(sequence)
    sys.stdout.flush()


async def _run_clipboard_command(command: ClipboardCommand, text: str) -> str:
    """执行剪贴板命令并返回 UTF-8 标准输出。"""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(text.encode(const.CHARSET))
    except OSError as error:
        raise ClipboardError(f"{type(error).__name__}: {error}") from error

    if process.returncode != 0:
        message = _decode_process_text(stderr).strip()
        raise ClipboardError(
            message or f"clipboard command exited with status {process.returncode}"
        )
    return _decode_process_text(stdout)


def _decode_process_text(data: bytes) -> str:
    """解码子进程输出文本。"""
    encoding = const.CHARSET if os.name != "nt" else "utf-8-sig"
    return data.decode(encoding, errors="replace")


if __name__ == '__main__':
    pass
