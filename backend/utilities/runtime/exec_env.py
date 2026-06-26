# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import sys
import shutil
import typing
import platform
from pathlib import Path
from functools import lru_cache


@lru_cache(maxsize=1)
def _cached_exec_env() -> dict[str, typing.Any]:
    """返回缓存后的本地执行环境信息。"""
    return {
        "platform"  : detect_platform(),
        "shell"     : detect_shell(),
        "runtimes"  : detect_runtimes(),
        "tools"     : detect_tools(),
        "env"       : detect_env(),
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
        "python" : runtime_bin(["python", "python3", "py"]),
        "node"   : runtime_bin(["node"]),
        "npm"    : runtime_bin(["npm"]),
        "java"   : runtime_bin(["java"]),
        "javac"  : runtime_bin(["javac"]),
        "maven"  : runtime_bin(["mvn"]),
        "gradle" : runtime_bin(["gradle"]),
        "go"     : runtime_bin(["go"]),
        "git"    : runtime_bin(["git"])
    }


def detect_tools() -> dict[str, typing.Any]:
    """检测随本地运行时提供给远端感知的命令行工具。"""
    return {
        "adb"      : tool_bin("adb"),
        "k6"       : tool_bin("k6"),
        "ffmpeg"   : tool_bin("ffmpeg"),
        "ffprobe"  : tool_bin("ffprobe"),
        "ast-grep" : tool_bin("ast-grep"),
        "rg"       : tool_bin("rg"),
        "jq"       : tool_bin("jq"),
        "framix"   : tool_bin("framix"),
        "memrix"   : tool_bin("memrix")
    }


def detect_env() -> dict[str, str]:
    """读取允许上报给远端的本地环境变量。"""
    names = [
        "JAVA_HOME",
        "MAVEN_HOME",
        "GRADLE_HOME",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "NVM_DIR"
    ]
    return {
        name: value
        for name in names
        if (value := _clean_env(name))
    }


def tool_bin(
    command: str,
    *,
    path_required: bool = False
) -> dict[str, typing.Any]:
    """解析可供远端生成命令的本地工具信息。"""
    executable = shutil.which(command)
    result: dict[str, typing.Any] = {
        "available"     : bool(executable),
        "command"       : command,
        "path"          : executable or "",
        "path_required" : bool(path_required),
        "source"        : tool_source(executable)
    }
    return result


def runtime_bin(
    candidates: list[str]
) -> dict[str, typing.Any]:
    """解析运行时可执行文件。"""
    for candidate in candidates:
        executable = shutil.which(candidate)
        if not executable:
            continue

        result: dict[str, typing.Any] = {
            "available"  : True,
            "executable" : executable
        }
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
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "go.mod",
        "Cargo.toml"
    ]
    markers = [name for name in marker_names if (root / name).exists()]
    return {
        "root"               : str(root),
        "markers"            : markers,
        "package_manager"    : detect_package_manager(root),
        "python_virtualenvs" : detect_python_virtualenvs(root),
        "source"             : "runtime_service_process_cwd"
    }


def detect_package_manager(root: Path) -> str:
    """根据锁文件推断 Node 包管理器。"""
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lock").exists() or (root / "bun.lockb").exists():
        return "bun"
    if (root / "package-lock.json").exists():
        return "npm"
    return "npm" if (root / "package.json").exists() else ""


def detect_python_virtualenvs(root: Path) -> list[dict[str, typing.Any]]:
    """扫描工作区常见 Python 虚拟环境。"""
    out: list[dict[str, typing.Any]] = []
    for name in ("venv", ".venv"):
        path = root / name
        executable = venv_python(path)
        if executable is None:
            continue
        relative_python = executable.relative_to(root).as_posix()
        out.append({
            "name"            : name,
            "path"            : str(path),
            "relative_path"   : name,
            "python"          : str(executable),
            "relative_python" : relative_python,
            "available"       : True
        })
    return out


def venv_python(root: Path) -> Path | None:
    """返回虚拟环境中的 Python 可执行文件。"""
    folders = [
        root / "Scripts" if os.name == "nt" else root / "bin",
        root / "bin",
        root / "Scripts"
    ]
    names = ("python.exe", "python") if os.name == "nt" else ("python", "python3")

    for folder in folders:
        if not folder.is_dir():
            continue
        for name in names:
            executable = folder / name
            if executable.exists():
                return executable
    return None


def tool_source(executable: str | None) -> str:
    """标记工具来自普通 PATH 还是随包 bundled requires。"""
    if not executable:
        return "missing"
    parts = Path(executable).resolve().parts
    return "bundled" if "requires" in parts else "path"


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
