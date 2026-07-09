# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import shutil
import typing
import asyncio
from mind_nova import const


class ClipboardError(RuntimeError):
    """剪贴板写入失败。"""


async def copy_text_to_clipboard(text: str) -> None:
    """把文本写入系统剪贴板。"""
    value = str(text or "")
    if not value:
        raise ClipboardError("clipboard text is empty")

    command = _clipboard_command()
    if command is None:
        raise ClipboardError("clipboard command unavailable")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate(value.encode(const.CHARSET))
    except OSError as error:
        raise ClipboardError(f"{type(error).__name__}: {error}") from error

    if process.returncode != 0:
        message = _decode_process_text(stderr).strip()
        raise ClipboardError(message or "clipboard command failed")


def _clipboard_command() -> typing.Optional[list[str]]:
    """返回当前平台可用的剪贴板命令。"""
    if sys.platform == "win32":
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        if executable:
            return [
                executable,
                "-NoProfile",
                "-Command",
                (
                    "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
                    "$text = [Console]::In.ReadToEnd(); "
                    "Set-Clipboard -Value $text"
                )
            ]
        return None

    if sys.platform == "darwin":
        executable = shutil.which("pbcopy")
        return [executable] if executable else None

    for command in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"]
    ):
        executable = shutil.which(command[0])
        if executable:
            return [executable, *command[1:]]

    return None


def _decode_process_text(data: bytes) -> str:
    """解码子进程输出文本。"""
    encoding = const.CHARSET if os.name != "nt" else "utf-8-sig"
    return data.decode(encoding, errors="replace")


if __name__ == '__main__':
    pass
