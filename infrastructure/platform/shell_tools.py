# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import stat
import typing
from pathlib import Path


SHELL_TOOL_LAYOUT: dict[str, tuple[str, str]] = {
    "7z": ("7z", "7z"),
    "ast-grep": ("ast-grep", "ast-grep"),
    "jq": ("jq", "jq"),
    "rg": ("ripgrep", "rg"),
    "sqlite3": ("sqlite3", "sqlite3"),
    "xh": ("xh", "xh"),
    "yq": ("yq", "yq"),
}


def route_shell_tools(supports: typing.Any) -> dict[str, str]:
    """把可用的本地命令行工具目录加入 PATH。"""
    root = Path(str(supports or "")).expanduser()

    routed: dict[str, str] = {}

    for tool, (folder_name, command_name) in SHELL_TOOL_LAYOUT.items():
        folder     = root / folder_name
        executable = folder / executable_name(command_name)

        if not folder.is_dir() or not executable.exists():
            continue

        ensure_executable(executable)
        prepend_path(folder)
        routed[tool] = str(folder)

    return routed


def executable_name(command: str) -> str:
    """返回当前系统的可执行文件名。"""
    if sys.platform.startswith("win") and not command.endswith(".exe"):
        return f"{command}.exe"
    return command


def ensure_executable(path: Path) -> None:
    """在需要时补齐可执行权限。"""
    if sys.platform != "darwin" or not path.is_file():
        return None

    mode = path.stat().st_mode

    desired = mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    if desired != mode:
        path.chmod(desired)


def prepend_path(folder: Path) -> None:
    """把目录加入 PATH 开头，已存在时不重复加入。"""
    text    = str(folder)
    current = os.environ.get("PATH", "")
    parts   = [item for item in current.split(os.pathsep) if item]

    if text in parts:
        return None
    os.environ["PATH"] = text + (os.pathsep + current if current else "")


if __name__ == '__main__':
    pass
