# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import shutil
import typing
from backend.utilities.command_heads import is_python_head


class PythonRuntimeResolver(object):
    """只为 native shell_exec 解析 Python 命令，避免污染底层 Flux。"""

    PYTHON_FALLBACKS: typing.ClassVar[tuple[str, ...]] = ("python", "python3", "py")

    @classmethod
    def resolve_shell_command(
        cls,
        command: list[str],
        *,
        env: dict[str, str] | None = None
    ) -> dict[str, typing.Any]:
        """解析 shell_exec 的 Python 命令，返回执行命令或云端交接诊断。"""
        cmd = [str(item) for item in (command or []) if str(item or "").strip()]
        if not cmd or not is_python_head(cmd[0]):
            return {"ok": True, "command": cmd, "changed": False}

        resolved = cls._resolve_from_path(cmd[0], env=env)
        if resolved:
            return {
                "ok": True,
                "command": [resolved, *cmd[1:]],
                "changed": resolved != cmd[0],
                "python_executable": resolved,
                "python_runtime_source": "path"
            }

        return {
            "ok": False,
            "reason": "local_python_unavailable",
            "command": cmd,
            "execution_target": "cloud_sandbox",
            "requires_cloud_sandbox": True,
            "suggested_next_action": "use_cloud_sandbox_or_configure_python",
            "diagnostic": (
                "local Python is unavailable from PATH; "
                "run in cloud sandbox or configure a real Python executable"
            )
        }

    @classmethod
    def _resolve_from_path(
        cls,
        requested: str,
        *,
        env: dict[str, str] | None
    ) -> str:
        names = [requested, *[item for item in cls.PYTHON_FALLBACKS if item != requested]]
        for name in names:
            resolved = cls._which(name, env=env)
            candidate = cls._real_python_candidate(resolved)
            if candidate:
                return candidate
        return ""

    @classmethod
    def _real_python_candidate(cls, value: typing.Any) -> str:
        path = str(value or "").strip()
        if not path:
            return ""
        name = path.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
        if name in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
            return path
        if name.startswith("python"):
            return path
        return ""

    @staticmethod
    def _which(program: str, *, env: dict[str, str] | None = None) -> str:
        path = (env or {}).get("PATH") if env is not None else None
        return shutil.which(program, path=path) if path is not None else (shutil.which(program) or "")


if __name__ == '__main__':
    pass
