# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import copy
import shutil
import typing
import platform
from pathlib import Path
from functools import lru_cache

WORKSPACE_MARKERS = [
    ".git",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "pdm.lock",
    "environment.yml",
    "environment.yaml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "mvnw",
    "mvnw.cmd",
    "gradlew",
    "gradlew.bat",
    "go.mod",
    "go.work",
    "Cargo.toml",
    "Cargo.lock",
    "global.json",
    "composer.json",
    "composer.lock",
    "Gemfile",
    "Gemfile.lock",
    "CMakeLists.txt",
    "Makefile"
]


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


def clear_exec_env_cache() -> None:
    """清理本地执行环境缓存。"""
    _cached_exec_env.cache_clear()


def build_runtime_exec_env(
    *,
    service_exec_env: typing.Optional[dict[str, typing.Any]] = None
) -> dict[str, typing.Any]:
    """组合对外上报的执行环境，外部 provider 放在 providers 下。"""
    data = copy.deepcopy(exec_env())

    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    else:
        providers = copy.deepcopy(providers)

    if isinstance(service_exec_env, dict):
        providers["helix"] = copy.deepcopy(service_exec_env)

    data["providers"] = providers
    return data


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
        "7z"       : tool_bin("7z"),
        "ast-grep" : tool_bin("ast-grep"),
        "rg"       : tool_bin("rg"),
        "jq"       : tool_bin("jq"),
        "sqlite3"  : tool_bin("sqlite3"),
        "yq"       : tool_bin("yq")
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
    return {
        "root"     : str(root),
        "markers"  : existing_names(root, WORKSPACE_MARKERS),
        "projects" : detect_workspace_projects(root),
        "source"   : "client_process_cwd"
    }


def detect_workspace_projects(root: Path) -> dict[str, dict[str, typing.Any]]:
    """按项目标记返回当前工作区的项目环境。"""
    detectors = [
        ("python", detect_python_project),
        ("node", detect_node_project),
        ("java", detect_java_project),
        ("go", detect_go_project),
        ("rust", detect_rust_project),
        ("dotnet", detect_dotnet_project),
        ("php", detect_php_project),
        ("ruby", detect_ruby_project),
        ("cpp", detect_cpp_project)
    ]
    projects: dict[str, dict[str, typing.Any]] = {}

    for name, detector in detectors:
        data = detector(root)
        if data:
            projects[name] = data

    return projects


def detect_python_project(root: Path) -> dict[str, typing.Any]:
    """返回 Python 项目的工作区环境。"""
    markers = existing_names(root, [
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "poetry.lock",
        "uv.lock",
        "pdm.lock",
        "environment.yml",
        "environment.yaml"
    ])

    virtualenvs = detect_python_virtualenvs(root)
    active_env  = active_python_environment(root)

    if not markers and not virtualenvs and not active_env:
        return {}

    result: dict[str, typing.Any] = {
        "markers"     : markers,
        "virtualenvs" : virtualenvs
    }
    if active_env:
        result["active_environment"] = active_env
    return result


def detect_node_project(root: Path) -> dict[str, typing.Any]:
    """返回 Node 项目的工作区环境。"""
    markers = existing_names(root, [
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "bun.lockb"
    ])
    if not markers:
        return {}

    return {
        "markers"         : markers,
        "package_manager" : detect_package_manager(root),
        "node_modules"    : directory_info(root, "node_modules")
    }


def detect_java_project(root: Path) -> dict[str, typing.Any]:
    """返回 Java 项目的工作区环境。"""
    markers = existing_names(root, [
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "mvnw",
        "mvnw.cmd",
        "gradlew",
        "gradlew.bat"
    ])
    if not markers:
        return {}

    build_tools: list[str] = []
    if "pom.xml" in markers or "mvnw" in markers or "mvnw.cmd" in markers:
        build_tools.append("maven")

    if any(name in markers for name in (
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradlew",
        "gradlew.bat"
    )):
        build_tools.append("gradle")

    return {
        "markers"        : markers,
        "build_tools"    : build_tools,
        "maven_wrapper"  : first_file_info(root, ["mvnw", "mvnw.cmd"]),
        "gradle_wrapper" : first_file_info(root, ["gradlew", "gradlew.bat"])
    }


def detect_go_project(root: Path) -> dict[str, typing.Any]:
    """返回 Go 项目的工作区环境。"""
    markers = existing_names(root, ["go.mod", "go.work"])
    if not markers:
        return {}

    return {
        "markers"        : markers,
        "module_file"    : file_info(root, "go.mod"),
        "workspace_file" : file_info(root, "go.work")
    }


def detect_rust_project(root: Path) -> dict[str, typing.Any]:
    """返回 Rust 项目的工作区环境。"""
    markers = existing_names(root, ["Cargo.toml", "Cargo.lock"])
    if not markers:
        return {}

    return {
        "markers"  : markers,
        "manifest" : file_info(root, "Cargo.toml"),
        "lockfile" : file_info(root, "Cargo.lock")
    }


def detect_dotnet_project(root: Path) -> dict[str, typing.Any]:
    """返回 .NET 项目的工作区环境。"""
    project_files = root_glob_names(root, ["*.sln", "*.csproj", "*.fsproj", "*.vbproj"])

    markers = existing_names(root, ["global.json"]) + project_files
    if not markers:
        return {}

    return {
        "markers"       : markers,
        "global_json"   : file_info(root, "global.json"),
        "project_files" : [file_info(root, name) for name in project_files]
    }


def detect_php_project(root: Path) -> dict[str, typing.Any]:
    """返回 PHP 项目的工作区环境。"""
    markers = existing_names(root, ["composer.json", "composer.lock"])
    if not markers:
        return {}

    return {
        "markers"    : markers,
        "manifest"   : file_info(root, "composer.json"),
        "lockfile"   : file_info(root, "composer.lock"),
        "vendor_dir" : directory_info(root, "vendor")
    }


def detect_ruby_project(root: Path) -> dict[str, typing.Any]:
    """返回 Ruby 项目的工作区环境。"""
    markers = existing_names(root, ["Gemfile", "Gemfile.lock"])
    if not markers:
        return {}

    return {
        "markers"  : markers,
        "gemfile"  : file_info(root, "Gemfile"),
        "lockfile" : file_info(root, "Gemfile.lock")
    }


def detect_cpp_project(root: Path) -> dict[str, typing.Any]:
    """返回 C/C++ 项目的工作区环境。"""
    markers = existing_names(root, ["CMakeLists.txt", "Makefile"])
    if not markers:
        return {}

    return {
        "markers"    : markers,
        "cmake_file" : file_info(root, "CMakeLists.txt"),
        "makefile"   : file_info(root, "Makefile")
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


def active_python_environment(root: Path) -> dict[str, typing.Any]:
    """返回当前激活的 Python 环境。"""
    for env_name, kind in (("VIRTUAL_ENV", "virtualenv"), ("CONDA_PREFIX", "conda")):
        value = _clean_env(env_name)
        if not value:
            continue

        path = Path(value).expanduser()

        result: dict[str, typing.Any] = {
            "kind"   : kind,
            "env"    : env_name,
            "path"   : str(path),
            "inside_workspace": is_relative_to(path, root)
        }

        if result["inside_workspace"]:
            result["relative_path"] = path.resolve().relative_to(root).as_posix()
        return result

    return {}


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


def existing_names(root: Path, names: list[str]) -> list[str]:
    """返回根目录下存在的指定文件或目录名。"""
    return [name for name in names if (root / name).exists()]


def root_glob_names(root: Path, patterns: list[str]) -> list[str]:
    """返回根目录下匹配模式的文件名。"""
    out: list[str] = []
    for pattern in patterns:
        out.extend(path.name for path in root.glob(pattern) if path.exists())
    return sorted(set(out))


def file_info(root: Path, name: str) -> dict[str, typing.Any]:
    """返回根目录下文件的路径信息。"""
    path = root / name
    if not path.exists():
        return {}

    return {
        "path"          : str(path),
        "relative_path" : name
    }


def first_file_info(root: Path, names: list[str]) -> dict[str, typing.Any]:
    """返回第一个存在文件的路径信息。"""
    for name in names:
        data = file_info(root, name)
        if data:
            return data

    return {}


def directory_info(root: Path, name: str) -> dict[str, typing.Any]:
    """返回根目录下目录的可用状态。"""
    path = root / name

    return {
        "available"     : path.is_dir(),
        "path"          : str(path) if path.is_dir() else "",
        "relative_path" : name
    }


def is_relative_to(path: Path, root: Path) -> bool:
    """判断路径是否位于根目录内。"""
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False

    return True


def tool_source(executable: str | None) -> str:
    """标记工具来自普通 PATH 还是随包目录。"""
    if not executable:
        return "missing"
    parts = Path(executable).resolve().parts
    if "schematic" in parts and "supports" in parts:
        return "bundled"

    return "path"


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
