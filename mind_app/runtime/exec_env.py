# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import shutil
import typing
import platform
import subprocess
from pathlib import Path
from functools import lru_cache
from mind_nova import const


@lru_cache(maxsize=1)
def _cached_exec_env() -> dict[str, typing.Any]:
    """返回缓存后的本地执行环境信息。"""
    return {
        "platform"  : detect_platform(),
        "shell"     : detect_shell(),
        "runtimes"  : detect_runtimes(),
        "tools"     : detect_tools(),
        "workspace" : detect_workspace()
    }


def exec_env() -> dict[str, typing.Any]:
    """返回本地执行环境信息。"""
    return _cached_exec_env()


def detect_platform() -> dict[str, typing.Any]:
    """返回平台基础信息。"""
    return {
        "sys"                 : sys.platform,
        "system"              : platform.system().strip().lower() or "unknown",
        "machine"             : platform.machine(),
        "os_name"             : os.name,
        "path_separator"      : os.sep,
        "path_list_separator" : os.pathsep
    }


def detect_shell() -> dict[str, typing.Any]:
    """解析当前主机的默认 shell 信息。"""
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            name = _shell_name(powershell)
            return {
                "name"       : name,
                "syntax"     : "powershell",
                "executable" : powershell,
                "prefix"     : [powershell, "-NoProfile", "-Command"],
                "source"     : "path"
            }

        comspec = _clean_env("COMSPEC") or "cmd.exe"
        return {
            "name"       : "cmd",
            "syntax"     : "cmd",
            "executable" : comspec,
            "prefix"     : [comspec, "/d", "/s", "/c"],
            "source"     : "env" if _clean_env("COMSPEC") else "default"
        }

    shell    = _clean_env("SHELL") or "/bin/sh"
    resolved = shutil.which(shell) or shell

    return {
        "name"       : _shell_name(resolved),
        "syntax"     : "posix",
        "executable" : resolved,
        "prefix"     : [resolved, "-lc"],
        "source"     : "env" if _clean_env("SHELL") else "default"
    }


def detect_runtimes() -> dict[str, typing.Any]:
    """检测常见本地运行时。"""
    return {
        "python" : runtime_bin(["python", "python3", "py"], version_args=["--version"]),
        "node"   : runtime_bin(["node"], version_args=["--version"]),
        "npm"    : runtime_bin(["npm"], version_args=["--version"]),
        "java"   : runtime_bin(["java"], version_args=["-version"]),
        "javac"  : runtime_bin(["javac"], version_args=["-version"]),
        "maven"  : runtime_bin(["mvn"], version_args=["-version"]),
        "gradle" : runtime_bin(["gradle"], version_args=["-version"]),
        "go"     : runtime_bin(["go"], version_args=["version"]),
        "git"    : runtime_bin(["git"], version_args=["--version"])
    }


def detect_tools() -> dict[str, typing.Any]:
    """检测随本地运行时提供给远端感知的命令行工具。"""
    return {
        "adb"    : tool_bin("adb", version_args=["version"]),
        "ffmpeg" : tool_bin("ffmpeg", version_args=["-version"]),
        "k6"     : tool_bin("k6", version_args=["version"]),
        "rg"     : tool_bin("rg", version_args=["--version"])
    }


def tool_bin(
    command: str,
    *,
    version_args: list[str],
    timeout_sec: float = 1.5,
    path_required: bool = False
) -> dict[str, typing.Any]:
    """解析可供远端生成命令的本地工具信息。"""
    executable = shutil.which(command)
    result: dict[str, typing.Any] = {
        "available"     : bool(executable),
        "command"       : command,
        "path"          : executable or "",
        "path_required" : bool(path_required)
    }
    if not executable:
        return result

    # version = _runtime_version(executable, version_args, timeout_sec=timeout_sec)
    # if version:
    #     result["version"] = version
    return result


def runtime_bin(
    candidates: list[str],
    *,
    version_args: list[str],
    timeout_sec: float = 1.5
) -> dict[str, typing.Any]:
    """解析运行时可执行文件并读取版本信息。"""
    for candidate in candidates:
        executable = shutil.which(candidate)
        if not executable:
            continue

        result: dict[str, typing.Any] = {
            "available"  : True,
            "executable" : executable
        }
        version = _runtime_version(executable, version_args, timeout_sec=timeout_sec)
        if version:
            result["version"] = version
        return result

    return {"available": False}


def detect_workspace() -> dict[str, typing.Any]:
    """返回当前进程工作目录和项目标记。"""
    root = Path.cwd().resolve()
    marker_names = [
        ".git",
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "package.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "go.mod",
        "Cargo.toml"
    ]
    markers = [name for name in marker_names if (root / name).exists()]
    return {
        "root"    : str(root),
        "markers" : markers,
        "source"  : "client_process_cwd"
    }


def _runtime_version(
    executable: str,
    args: list[str],
    *,
    timeout_sec: float
) -> str:
    """执行版本查询命令并返回首行输出。"""
    try:
        completed = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding=const.CHARSET,
            errors="replace",
            check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    output = "\n".join(
        item.strip()
        for item in (completed.stdout, completed.stderr)
        if item and item.strip()
    )
    return output.splitlines()[0].strip() if output else ""


def _shell_name(executable: str) -> str:
    """从 shell 可执行文件路径提取名称。"""
    name = str(executable or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name or "shell"


def _clean_env(name: str) -> str:
    """读取环境变量并去除首尾空白。"""
    return str(os.environ.get(name) or "").strip()


if __name__ == '__main__':
    pass
