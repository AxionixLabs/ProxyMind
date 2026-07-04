# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShellRuntime(object):
    """记录 shell 字符串执行时使用的进程前缀和语法类型。"""

    name: str
    prefix: list[str]
    syntax: str
    executable: str
    source: str


class ShellRuntimeResolver(object):
    """解析 shell 字符串执行所需的本地运行时信息。"""

    @classmethod
    def resolve(
        cls,
        *,
        env: dict[str, str] | None = None
    ) -> ShellRuntime:
        """返回可用于执行 shell 字符串的运行时信息。"""
        env_shell = cls._env_shell(env=env)
        if env_shell:
            return cls._runtime_from_shell(env_shell, env=env, source="env")

        return cls._platform_default(env=env)

    @classmethod
    def _runtime_from_shell(
        cls,
        shell: list[str],
        *,
        env: dict[str, str] | None,
        source: str
    ) -> ShellRuntime:
        """根据 shell 可执行文件构造运行时信息。"""
        executable = shell[0]
        basename   = cls._basename(executable)
        resolved   = cls._which(executable, env=env) or executable
        tail       = shell[1:]

        if basename in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
            return ShellRuntime(
                name=cls._runtime_name(basename),
                prefix=[resolved, *(tail or ["-NoProfile", "-Command"])],
                syntax="powershell",
                executable=resolved,
                source=source
            )
        if basename in {"cmd", "cmd.exe"}:
            return ShellRuntime(
                name="cmd",
                prefix=[resolved, *(tail or ["/d", "/s", "/c"])],
                syntax="cmd",
                executable=resolved,
                source=source
            )

        return ShellRuntime(
            name=cls._runtime_name(basename),
            prefix=[resolved, *(tail or ["-lc"])],
            syntax="posix",
            executable=resolved,
            source=source
        )

    @classmethod
    def _platform_default(
        cls,
        *,
        env: dict[str, str] | None
    ) -> ShellRuntime:
        """返回当前平台的默认运行时信息。"""
        if os.name == "nt":
            powershell = cls._which("pwsh", env=env) or cls._which("powershell", env=env)
            if powershell:
                return ShellRuntime(
                    name=cls._runtime_name(cls._basename(powershell)),
                    prefix=[powershell, "-NoProfile", "-Command"],
                    syntax="powershell",
                    executable=powershell,
                    source="path"
                )

            comspec = cls._env_get("COMSPEC", env=env) or "cmd.exe"
            return ShellRuntime(
                name="cmd",
                prefix=[comspec, "/d", "/s", "/c"],
                syntax="cmd",
                executable=comspec,
                source="env" if cls._env_get("COMSPEC", env=env) else "default"
            )

        shell = cls._env_get("SHELL", env=env) or "/bin/sh"

        return cls._runtime_from_shell(
            shell=[shell],
            env=env,
            source="env" if cls._env_get("SHELL", env=env) else "default"
        )

    @classmethod
    def _env_shell(
        cls,
        *,
        env: dict[str, str] | None
    ) -> list[str]:
        """读取平台约定的 shell 环境变量。"""
        value = cls._env_get("SHELL", env=env)
        if value and os.name != "nt":
            return [value]
        return []

    @staticmethod
    def _env_get(key: str, *, env: dict[str, str] | None) -> str:
        """读取环境变量并返回去除首尾空白后的文本。"""
        if env is not None and key in env:
            return str(env.get(key) or "").strip()
        return str(os.environ.get(key) or "").strip()

    @staticmethod
    def _which(program: str, *, env: dict[str, str] | None) -> str:
        """在 PATH 中查找可执行文件。"""
        path = (env or {}).get("PATH") if env is not None else None
        return shutil.which(program, path=path) if path is not None else (shutil.which(program) or "")

    @staticmethod
    def _basename(value: str) -> str:
        """返回路径中的文件名小写形式。"""
        return str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()

    @staticmethod
    def _runtime_name(basename: str) -> str:
        """根据文件名返回运行时名称。"""
        if basename in {"pwsh", "pwsh.exe"}:
            return "pwsh"
        if basename in {"powershell", "powershell.exe"}:
            return "powershell"
        if basename in {"cmd", "cmd.exe"}:
            return "cmd"

        return basename.rsplit(".", 1)[0] if basename else "shell"


if __name__ == '__main__':
    pass

