# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import shutil


class NativeCommandRuntime(object):
    """解析 Windows command shim，保持 Flux 只负责启动进程。"""

    @staticmethod
    def resolve_command(
        command: list[str],
        *,
        env: dict[str, str] | None = None
    ) -> list[str]:
        cmd = [str(item) for item in (command or []) if str(item or "").strip()]
        if not cmd:
            return cmd

        program = str(cmd[0] or "").strip()
        resolved = NativeCommandRuntime._which(program, env=env) or program
        suffix = os.path.splitext(resolved)[1].lower()
        if suffix in {".cmd", ".bat"}:
            comspec = (env or {}).get("COMSPEC") or os.environ.get("COMSPEC") or "cmd.exe"
            return [comspec, "/c", resolved, *cmd[1:]]
        if resolved:
            return [resolved, *cmd[1:]]
        return cmd

    @staticmethod
    def _which(program: str, *, env: dict[str, str] | None = None) -> str:
        path = (env or {}).get("PATH") if env is not None else None
        return shutil.which(program, path=path) if path is not None else (shutil.which(program) or "")


if __name__ == '__main__':
    pass
