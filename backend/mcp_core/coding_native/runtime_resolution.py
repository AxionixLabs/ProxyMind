# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import shutil
import typing
from backend.utilities.command_heads import is_python_head


class RuntimeResolver(object):
    """为 native shell_exec 解析常见语言运行时，并生成统一诊断。"""

    RUNTIME_SPECS: typing.ClassVar[dict[str, dict[str, typing.Any]]] = {
        "python": {
            "heads": {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"},
            "fallbacks": ("python", "python3", "py"),
            "cloud_sandbox_supported": True,
            "reason": "local_python_unavailable",
            "suggested_next_action": "use_cloud_sandbox_or_configure_python",
        },
        "node": {
            "heads": {"node", "node.exe", "npm", "npm.cmd", "npx", "npx.cmd"},
            "fallbacks": (),
            "cloud_sandbox_supported": False,
            "reason": "local_runtime_unavailable",
            "suggested_next_action": "install_node_or_skip_local_validation",
        },
        "java": {
            "heads": {
                "java", "java.exe", "javac", "javac.exe",
                "mvn", "mvn.cmd", "gradle", "gradle.bat"
            },
            "fallbacks": (),
            "cloud_sandbox_supported": False,
            "reason": "local_runtime_unavailable",
            "suggested_next_action": "install_jdk_or_skip_local_validation",
        },
        "go": {
            "heads": {"go", "go.exe"},
            "fallbacks": (),
            "cloud_sandbox_supported": False,
            "reason": "local_runtime_unavailable",
            "suggested_next_action": "install_go_or_skip_local_validation",
        },
    }

    @classmethod
    def resolve_shell_command(
        cls,
        command: list[str],
        *,
        env: dict[str, str] | None = None
    ) -> dict[str, typing.Any]:
        """解析 shell_exec 命令头对应的语言运行时。"""
        cmd = [str(item) for item in (command or []) if str(item or "").strip()]
        if not cmd:
            return {"ok": True, "command": cmd, "changed": False}

        runtime_name = cls._runtime_name(cmd[0])
        if not runtime_name:
            return {"ok": True, "command": cmd, "changed": False}

        spec = cls.RUNTIME_SPECS[runtime_name]
        resolved = cls._resolve_from_path(cmd[0], spec=spec, env=env)
        if resolved:
            return {
                "ok": True,
                "command": [resolved, *cmd[1:]],
                "changed": resolved != cmd[0],
                "runtime": {
                    "name": runtime_name,
                    "source": "path",
                    "executable": resolved,
                },
            }

        cloud_supported = bool(spec.get("cloud_sandbox_supported"))
        return {
            "ok": False,
            "reason": str(spec.get("reason") or "local_runtime_unavailable"),
            "runtime": {
                "name": runtime_name,
                "source": "path",
                "executable": None,
                "cloud_sandbox_supported": cloud_supported,
            },
            "command": cmd,
            "execution_target": "cloud_sandbox" if cloud_supported else "local",
            "requires_cloud_sandbox": cloud_supported,
            "cloud_sandbox_supported": cloud_supported,
            "suggested_next_action": str(spec.get("suggested_next_action") or "configure_runtime"),
            "diagnostic": cls._diagnostic(runtime_name, cloud_supported=cloud_supported),
        }

    @classmethod
    def _resolve_from_path(
        cls,
        requested: str,
        *,
        spec: dict[str, typing.Any],
        env: dict[str, str] | None
    ) -> str:
        fallbacks = tuple(str(item) for item in spec.get("fallbacks") or ())
        names = [requested, *[item for item in fallbacks if item != requested]]
        for name in names:
            resolved = cls._which(name, env=env)
            if cls._runtime_candidate(resolved, spec=spec):
                return resolved
        return ""

    @classmethod
    def _runtime_name(cls, head: str) -> str:
        normalized = str(head or "").replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
        if is_python_head(normalized):
            return "python"
        for name, spec in cls.RUNTIME_SPECS.items():
            if normalized in spec.get("heads", set()):
                return name
        return ""

    @classmethod
    def _runtime_candidate(cls, value: typing.Any, *, spec: dict[str, typing.Any]) -> bool:
        path = str(value or "").strip()
        if not path:
            return False
        name = path.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
        if name in spec.get("heads", set()):
            return True
        return bool(name.startswith("python")) if "python" in spec.get("heads", set()) else False

    @staticmethod
    def _diagnostic(runtime_name: str, *, cloud_supported: bool) -> str:
        if runtime_name == "python":
            return (
                "local Python is unavailable from PATH; "
                "run in cloud sandbox or configure a real Python executable"
            )
        if cloud_supported:
            return f"local {runtime_name} runtime is unavailable from PATH"
        return (
            f"local {runtime_name} runtime is unavailable from PATH; "
            "cloud sandbox does not support this runtime"
        )

    @staticmethod
    def _which(program: str, *, env: dict[str, str] | None = None) -> str:
        path = (env or {}).get("PATH") if env is not None else None
        return shutil.which(program, path=path) if path is not None else (shutil.which(program) or "")


if __name__ == '__main__':
    pass
