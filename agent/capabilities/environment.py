# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import copy
import shutil
import typing
import datetime
from collections.abc import Mapping
from pathlib import Path
from agent.ports import CapabilityError
from agent.protocol.json_value import JsonValue
from protocol.schema.identifiers import short_uid
from protocol.schema.environment import (
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


class LocalEnvironmentSnapshotCapability:
    """按 Turn 捕获本机环境快照，并持有进程级静态事实缓存。"""

    def __init__(self) -> None:
        """创建尚未采集本机事实的环境能力。"""
        self._facts: _EnvironmentFacts | None = None

    def capture(
        self,
        *,
        cwd: str | Path,
        workspace_root: str | Path,
        providers: Mapping[str, Mapping[str, JsonValue]] | None = None,
    ) -> Mapping[str, JsonValue]:
        """捕获并校验一个新 Turn 使用的不可变环境事实。"""
        try:
            facts = copy.deepcopy(self._environment_facts())
            current_directory = _resolved_path(cwd)
            workspace_directory = _resolved_path(workspace_root)
        except (OSError, RuntimeError) as error:
            raise CapabilityError(
                "environment_capture_failed",
                "unable to capture the local environment",
                retryable=True,
                details={"exception_type": type(error).__name__},
            ) from error

        captured_at = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z")
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
            providers=_normalize_providers(providers),
            extensions={},
        )
        return normalize_client_environment_snapshot(snapshot)

    def clear_cache(self) -> None:
        """清除可复用的本机 shell 与工具事实。"""
        self._facts = None

    def _environment_facts(self) -> _EnvironmentFacts:
        """返回本进程缓存的本机静态事实。"""
        if self._facts is None:
            self._facts = _EnvironmentFacts(
                shell=_detect_shell(),
                tools=_detect_tools(),
            )
        return self._facts


def _normalize_providers(
    providers: Mapping[str, Mapping[str, JsonValue]] | None,
) -> dict[str, EnvironmentProvider]:
    """按正式协议校验所有服务执行面事实。"""
    if providers is None:
        return {}
    if not isinstance(providers, Mapping):
        raise TypeError("environment providers must be an object")
    normalized: dict[str, EnvironmentProvider] = {}
    for name, provider in providers.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("environment provider name is required")
        if not isinstance(provider, Mapping):
            raise TypeError("environment provider must be an object")
        normalized[name.strip()] = normalize_environment_provider(dict(provider))
    return normalized


def _detect_shell() -> EnvironmentShell:
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


def _detect_tools() -> dict[str, EnvironmentCapability]:
    """返回客户端明确声明的本地命令行工具能力。"""
    return {
        name: _tool_bin(name)
        for name in ("7z", "ast-grep", "rg", "jq", "sqlite3", "xh", "yq")
    }


def _tool_bin(command: str) -> EnvironmentCapability:
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


def _resolved_path(value: str | Path) -> Path:
    """解析调用方已经选定的 Turn 路径。"""
    return Path(value).expanduser().resolve()


def _shell_name(executable: str) -> str:
    """从 shell 可执行文件路径提取稳定名称。"""
    name = executable.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name or "shell"


def _clean_env(name: str) -> str:
    """读取并清理一个本机环境变量。"""
    return str(os.environ.get(name) or "").strip()


if __name__ == '__main__':
    pass
