# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import sys
import stat
import shutil
import typing
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from backend.mcp_hub.hub_manage import DeviceManage
from backend.utilities.paths import resource_path
from backend.utilities.runtime import (
    AppContext, Idle
)


def register_automator_tools(mcp: FastMCP, manage: DeviceManage, idle: Idle, ctx: AppContext) -> None:
    from backend.mcp_tools.automator import ctl_app
    from backend.mcp_tools.automator import ctl_file
    from backend.mcp_tools.automator import ctl_info
    from backend.mcp_tools.automator import ctl_keyevent
    from backend.mcp_tools.automator import ctl_monkey
    from backend.mcp_tools.automator import ctl_system
    from backend.mcp_tools.automator import ctl_ui
    from backend.mcp_tools.automator import ctl_zest

    ctl_app.bind(mcp, manage, ctx)
    ctl_file.bind(mcp, manage, ctx)
    ctl_info.bind(mcp, manage, ctx)
    ctl_keyevent.bind(mcp, manage, ctx)
    ctl_monkey.bind(mcp, manage, idle, ctx)
    ctl_system.bind(mcp, manage, ctx)
    ctl_ui.bind(mcp, manage, idle, ctx)
    ctl_zest.bind(mcp, manage, ctx)


def register_bench_tools(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:
    from backend.mcp_tools.bench import bench_framix
    from backend.mcp_tools.bench import bench_k6
    from backend.mcp_tools.bench import bench_memrix
    from backend.mcp_tools.bench import bench_nexus

    bench_framix.bind(mcp, idle, ctx)
    bench_k6.bind(mcp, idle, ctx)
    bench_memrix.bind(mcp, idle, ctx)
    bench_nexus.bind(mcp, idle, ctx)


def register_common_tools(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:
    from backend.mcp_tools.common import inspect
    from backend.mcp_tools.common import runtime
    from backend.mcp_tools.common import security

    inspect.bind(mcp, idle, ctx)
    runtime.bind(mcp, idle, ctx)
    security.bind(mcp, ctx)


def register_coding_tools(mcp: FastMCP, idle: Idle, ctx: AppContext) -> None:
    from backend.mcp_tools.coding import native

    native.bind(mcp, idle, ctx)


def register_media_tools(mcp: FastMCP, manage: DeviceManage, idle: Idle, ctx: AppContext) -> None:
    from backend.mcp_tools.media import audio
    from backend.mcp_tools.media import ffmpeg
    from backend.mcp_tools.media import screen

    audio.bind(mcp, idle, ctx)
    ffmpeg.bind(mcp, idle, ctx)
    screen.bind(mcp, manage, idle, ctx)


def initialize(
    tools: typing.Iterable[str] = (
        "adb", "ast-grep", "ffmpeg", "jq", "k6", "rg"
    )
) -> dict[str, typing.Any]:
    """
    初始化运行时工具目录。

    行为：
    - 按系统自动路由到 requires/windows 或 requires/macos
    - 将命中的工具目录 prepend 到 PATH
    - 不对缺失工具报错；具体工具调用时再由各域检查
    """
    requires_layout: dict[str, dict[str, list[str]]] = {
        "windows": {
            "adb"      : ["platform-tools"],
            "ast-grep" : ["ast-grep"],
            "ffmpeg"   : ["ffmpeg", "bin"],
            "jq"       : ["jq"],
            "k6"       : ["k6"],
            "rg"       : ["ripgrep"]
        },
        "macos": {
            "adb"      : ["platform-tools"],
            "ast-grep" : ["ast-grep"],
            "ffmpeg"   : ["ffmpeg", "bin"],
            "jq"       : ["jq"],
            "k6"       : ["k6"],
            "rg"       : ["ripgrep"]
        }
    }

    executable_names: dict[str, dict[str, str]] = {
        "windows": {
            "adb"      : "adb.exe",
            "ast-grep" : "ast-grep.exe",
            "ffmpeg"   : "ffmpeg.exe",
            "jq"       : "jq.exe",
            "k6"       : "k6.exe",
            "rg"       : "rg.exe"
        },
        "macos": {
            "adb"      : "adb",
            "ast-grep" : "ast-grep",
            "ffmpeg"   : "ffmpeg",
            "jq"       : "jq",
            "k6"       : "k6",
            "rg"       : "rg"
        }
    }

    def _platform_key() -> str:
        if sys.platform.startswith("win"):
            return "windows"
        if sys.platform == "darwin":
            return "macos"
        raise RuntimeError(f"Unsupported platform: {sys.platform}")

    def _requires_root() -> Path:
        return resource_path("requires", _platform_key())

    def _prepend_path(folder: Path) -> None:
        current = os.environ.get("PATH", "")
        parts = [p for p in current.split(os.pathsep) if p]
        if (text := str(folder)) in parts:
            return None
        os.environ["PATH"] = text + (os.pathsep + current if current else "")

    def _tool_dir(root: Path, tool: str) -> typing.Optional[Path]:
        parts = requires_layout.get(_platform_key(), {}).get(tool)
        if not parts:
            return None

        directory = root.joinpath(*parts)
        return directory if directory.exists() else None

    def _tool_exec(directory: Path, tool: str) -> typing.Optional[Path]:
        name = executable_names.get(_platform_key(), {}).get(tool)
        if not name:
            return None

        target = directory / name
        return target if target.exists() else None

    def _ensure_exec(target: Path) -> None:
        if _platform_key() != "macos" or not target.is_file():
            return None

        mode = target.stat().st_mode
        want = mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        if want != mode:
            target.chmod(want)

    _root = _requires_root()
    if not _root.exists():
        raise RuntimeError(f"requires root not found: {_root}")

    _routed: dict[str, str] = {}
    for _tool in dict.fromkeys(tools):
        if _directory := _tool_dir(_root, _tool):
            if _target := _tool_exec(_directory, _tool):
                _ensure_exec(_target)
            _prepend_path(_directory)
            _routed[_tool] = str(_directory)

    _available = {
        t: shutil.which(t) for t in dict.fromkeys(tools)
    }

    return {
        "ok"        : True,
        "platform"  : _platform_key(),
        "root"      : str(_root),
        "routed"    : _routed,
        "available" : _available
    }


def register_all_tools(mcp: FastMCP, manage: DeviceManage, idle: Idle, ctx: AppContext) -> None:
    initialize()
    register_automator_tools(mcp, manage, idle, ctx)
    register_bench_tools(mcp, idle, ctx)
    register_common_tools(mcp, idle, ctx)
    register_coding_tools(mcp, idle, ctx)
    register_media_tools(mcp, manage, idle, ctx)


if __name__ == '__main__':
    pass
