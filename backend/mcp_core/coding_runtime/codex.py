# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import shutil


class CodexRuntimeResolver(object):
    """解析 Codex CLI，隔离 Windows shim 兼容逻辑。"""

    @staticmethod
    def resolve_command(
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None
    ) -> list[str]:
        executable = CodexRuntimeResolver.find_executable(env=env)
        return CodexRuntimeResolver.wrap_windows_shim([executable, *(args or [])], env=env)

    @staticmethod
    def find_executable(*, env: dict[str, str] | None = None) -> str:
        path = (env or {}).get("PATH") if env is not None else None
        resolved = shutil.which("codex", path=path) if path is not None else shutil.which("codex")
        return resolved or "codex"

    @staticmethod
    def wrap_windows_shim(
        cmd: list[str],
        *,
        env: dict[str, str] | None = None
    ) -> list[str]:
        if not cmd:
            return cmd
        program = str(cmd[0] or "").strip()
        suffix = os.path.splitext(program)[1].lower()
        if suffix not in {".cmd", ".bat"}:
            return cmd
        comspec = (env or {}).get("COMSPEC") or os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/c", program, *cmd[1:]]


if __name__ == '__main__':
    pass
