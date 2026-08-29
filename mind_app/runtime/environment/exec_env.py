# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import datetime
import os
import shutil
import typing
from functools import lru_cache
from pathlib import Path
from mind_nova.identifiers import short_uid
from mind_nova.requests.environment import (
    ClientEnvironmentSnapshot,
    EnvironmentCapability,
    EnvironmentProvider,
    EnvironmentShell,
    EnvironmentWorkspace,
    normalize_client_environment_snapshot,
    normalize_environment_provider,
)


class _EnvironmentFacts(typing.TypedDict):
    """保存可跨 Turn 复用的本机工具与 shell 事实。"""

    shell: EnvironmentShell
    tools: dict[str, EnvironmentCapability]


@lru_cache(maxsize=1)
def _cached_environment_facts() -> _EnvironmentFacts:
    """采集并缓存不包含 Turn 路径和快照身份的本机事实。"""
    return _EnvironmentFacts(
        shell=detect_shell(),
        tools=detect_tools(),
    )


def exec_env(
    *,
    cwd: str | os.PathLike[str] | None = None,
    workspace_root: str | os.PathLike[str] | None = None,
) -> ClientEnvironmentSnapshot:
    """为一个新 Turn 构建完整的客户端环境快照。"""
    facts = copy.deepcopy(_cached_environment_facts())
    current_directory = _resolved_path(cwd, fallback=Path.cwd())
    workspace_directory = _resolved_path(
        workspace_root,
        fallback=current_directory,
    )
    captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )
    snapshot = ClientEnvironmentSnapshot(
        snapshot_id=f"envsnap_{short_uid(20)}",
        source="client",
        captured_at=captured_at,
        environment_id="local",
        cwd=str(current_directory),
        status="available",
        status_detail=None,
        shell=facts["shell"],
        workspace=EnvironmentWorkspace(
            root=str(workspace_directory),
            allowed_roots=[],
            source="client",
        ),
        tools=facts["tools"],
        providers={},
        extensions={},
    )
    return normalize_client_environment_snapshot(snapshot)


def clear_exec_env_cache() -> None:
    """清理本机 shell 与工具事实缓存。"""
    _cached_environment_facts.cache_clear()


def build_runtime_exec_env(
    *,
    cwd: str | os.PathLike[str],
    workspace_root: str | os.PathLike[str],
    service_exec_env: typing.Mapping[str, typing.Any] | None = None,
) -> ClientEnvironmentSnapshot:
    """构建本 Turn 使用的完整环境快照。"""
    snapshot = exec_env(cwd=cwd, workspace_root=workspace_root)
    providers: dict[str, EnvironmentProvider] = {}
    if service_exec_env is not None:
        providers["helix"] = normalize_environment_provider(
            dict(service_exec_env)
        )
    snapshot["providers"] = providers
    return normalize_client_environment_snapshot(snapshot)


def detect_shell() -> EnvironmentShell:
    """返回客户端默认 shell 事实。"""
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            return EnvironmentShell(
                name="powershell",
                syntax="powershell",
                executable=powershell,
                prefix=[powershell, "-NoProfile", "-Command"],
                source="path",
            )
        comspec = _clean_env("COMSPEC") or "cmd.exe"
        return EnvironmentShell(
            name="cmd",
            syntax="cmd",
            executable=comspec,
            prefix=[comspec, "/d", "/s", "/c"],
            source="env" if _clean_env("COMSPEC") else "default",
        )

    configured_shell = _clean_env("SHELL") or "/bin/sh"
    executable = shutil.which(configured_shell) or configured_shell
    return EnvironmentShell(
        name=_shell_name(executable),
        syntax="posix",
        executable=executable,
        prefix=[executable, "-lc"],
        source="env" if _clean_env("SHELL") else "default",
    )


def detect_tools() -> dict[str, EnvironmentCapability]:
    """返回客户端明确声明的本地命令行工具能力。"""
    return {
        name: tool_bin(name)
        for name in ("7z", "ast-grep", "rg", "jq", "sqlite3", "xh", "yq")
    }


def tool_bin(command: str) -> EnvironmentCapability:
    """返回单个命令行工具的固定能力结构。"""
    executable = shutil.which(command)
    return EnvironmentCapability(
        available=executable is not None,
        command=command,
        executable=executable,
        path=executable,
        version=None,
        source="path" if executable else "missing",
    )


def _resolved_path(
    value: str | os.PathLike[str] | None,
    *,
    fallback: Path,
) -> Path:
    return Path(value if value is not None else fallback).expanduser().resolve()


def _shell_name(executable: str) -> str:
    name = executable.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name or "shell"


def _clean_env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()
