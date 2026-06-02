# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import time
import typing
import asyncio
import subprocess
from loguru import logger
from backend.mcp_core.coding_native.base import NativeCodingComponent
from backend.mcp_core.coding_native.command_runtime import NativeCommandRuntime
from backend.mcp_core.coding_native.python_runtime import PythonRuntimeResolver
from backend.utilities.process import Flux
from backend.utilities.trace import summarize_command


class ShellGitTools(NativeCodingComponent):
    """提供 shell 执行、git 状态和 git diff 能力。"""

    READ_ONLY_COMMANDS = {
        "cat",
        "dir",
        "echo",
        "find",
        "git",
        "grep",
        "head",
        "ls",
        "python",
        "python3",
        "py",
        "rg",
        "tail",
        "type",
        "where"
    }

    READ_ONLY_PYTHON_FLAGS = {
        "-c",
        "-m",
        "--version",
        "-V"
    }

    READ_ONLY_GIT_SUBCOMMANDS = {
        "branch",
        "diff",
        "log",
        "rev-parse",
        "show",
        "status"
    }

    @staticmethod
    def _shell_output_next_steps(
        *,
        cmd: list[str],
        cwd: str,
        timeout_sec: int,
        stdout_truncated: bool,
        stderr_truncated: bool
    ) -> list[dict[str, typing.Any]]:
        """根据输出截断状态生成可选的后续执行建议。"""
        if not stdout_truncated and not stderr_truncated:
            return []

        return [
            {
                "tool": "shell_exec",
                "args": {
                    "command"     : cmd,
                    "cwd"         : cwd,
                    "timeout_sec" : timeout_sec
                },
                "reason": "rerun_with_narrower_or_larger_output"
            }
        ]

    def _is_git_workspace(self) -> bool:
        """判断当前工作区是否包含 git 仓库。"""
        git_marker = self.root / ".git"
        if git_marker.exists():
            return True

        env = os.environ.copy()
        try:
            result = subprocess.run(
                NativeCommandRuntime.resolve_command(["git", "rev-parse", "--is-inside-work-tree"], env=env),
                cwd=str(self.root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        return result.returncode == 0 and (result.stdout or b"").decode(errors="ignore").strip().lower() == "true"

    @classmethod
    def _audit_mode_for_command(cls, cmd: list[str], *, audit_files: bool) -> str:
        """根据命令类型选择审计强度。"""
        if not audit_files:
            return "off"
        if not cmd:
            return "full"

        executable = os.path.basename(str(cmd[0])).lower()
        if executable.endswith(".exe"):
            executable = executable[:-4]

        if executable == "git":
            subcommand = next((str(item).lower() for item in cmd[1:] if not str(item).startswith("-")), "")
            return "metadata" if subcommand in cls.READ_ONLY_GIT_SUBCOMMANDS else "full"

        if executable in {"python", "python3", "py"}:
            if len(cmd) >= 2 and str(cmd[1]) in cls.READ_ONLY_PYTHON_FLAGS:
                return "metadata"
            return "full"

        if executable in cls.READ_ONLY_COMMANDS:
            return "metadata"

        return "full"

    async def _git(self, args: list[str]) -> dict[str, typing.Any]:
        """在工作区根目录执行 git 子命令并返回统一结果。"""
        cmd     = ["git", *args]
        workdir = self._resolve(".")
        env     = os.environ.copy()
        started = time.perf_counter()

        proc = await Flux.cmd_link_exec(
            NativeCommandRuntime.resolve_command(cmd, env=env),
            cwd=str(workdir),
            env=env
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout, stderr = await proc.communicate()

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        raw_stdout = self._decode(stdout or b"")
        raw_stderr = self._decode(stderr or b"")
        exit_code  = int(proc.returncode or 0)
        ok         = (exit_code == 0) and not timed_out

        return {
            "text": f"git {'ok' if ok else 'failed'} exit_code={exit_code} elapsed_ms={elapsed_ms}",
            "attachments": [],
            "data": {
                "ok": ok,
                "command": cmd,
                "cwd": self._rel(workdir),
                "execution_target": "local",
                "requires_cloud_sandbox": False,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "elapsed_ms": elapsed_ms,
                "stdout": self._clip_output(raw_stdout, max_chars=self.max_output_chars),
                "stderr": self._clip_output(raw_stderr, max_chars=self.max_output_chars),
                "stdout_truncated": len(raw_stdout) > self.max_output_chars,
                "stderr_truncated": len(raw_stderr) > self.max_output_chars,
                "truncated": len(raw_stdout) > self.max_output_chars or len(raw_stderr) > self.max_output_chars
            },
            "logs": []
        }

    async def git_status(self) -> dict[str, typing.Any]:
        """返回当前工作区的 git status 摘要。"""
        if not self._is_git_workspace():
            return self._ok(
                "git status unavailable: workspace is not a git repository",
                stdout="",
                available=False
            )
        return await self._git(["status", "--short"])

    async def git_diff(
        self, path: str | None = None,
        max_chars: int = 24000
    ) -> dict[str, typing.Any]:
        """返回当前工作区或指定路径的 git diff。"""
        if not self._is_git_workspace():
            return self._ok(
                "git diff unavailable: workspace is not a git repository",
                stdout="",
                available=False
            )

        cmd = ["diff", "--"]
        if path:
            cmd.append(self._rel(self._resolve(path)))

        result     = await self._git(cmd)
        data       = result.get("data") or {}
        raw_stdout = str(data.get("stdout") or "")

        data["stdout"] = self._clip_output(raw_stdout, max_chars=max_chars)

        if len(raw_stdout) > max_chars:
            data["stdout_truncated"] = True
            data["truncated"] = True
            data["recommended_next_steps"] = [
                {
                    "tool": "git_diff",
                    "args": {
                        "path"      : path,
                        "max_chars" : min(max_chars * 2, self.max_output_chars)
                    },
                    "reason": "increase_limit"
                }
            ]
        return result

    async def shell_exec(
        self,
        *,
        command: list[str],
        cwd: str = ".",
        timeout_sec: int = 60,
        execution: dict[str, typing.Any] | None = None,
        audit_files: bool = True
    ) -> dict[str, typing.Any]:
        """按执行元数据运行 shell 命令，必要时返回云端沙盒交接结果。"""
        cmd = [str(item) for item in (command or []) if str(item or "").strip()]
        if not cmd:
            return self._fail("command_empty")

        policy = self.execution_metadata_policy(
            execution,
            command=cmd,
            cwd=cwd,
            timeout_sec=timeout_sec
        )
        if not policy["ok"]:
            return self._fail(
                policy["reason"],
                command=cmd,
                risk=policy.get("risk"),
                category=policy.get("category"),
                reasons=policy.get("reasons") or [],
                suggested_tool=policy.get("suggested_tool"),
                suggested_args=policy.get("suggested_args") or {},
                approval_required=bool(policy.get("approval_required")),
                project_types=policy.get("project_types") or [],
                execution_target=policy.get("execution_target"),
                requires_cloud_sandbox=bool(policy.get("requires_cloud_sandbox")),
                execution=policy.get("execution"),
                grant_id=policy.get("grant_id")
            )

        workdir = self._resolve(cwd)
        if not workdir.is_dir():
            return self._fail("cwd_not_directory", cwd=cwd)

        if policy.get("execution_target") == "cloud_sandbox":
            return self._ok(
                "shell exec requires cloud sandbox",
                ok=False,
                command=cmd,
                cwd=self._rel(workdir),
                risk=policy.get("risk"),
                category=policy.get("category"),
                risk_reasons=policy.get("reasons") or [],
                approval_required=bool(policy.get("approval_required")),
                execution_target="cloud_sandbox",
                requires_cloud_sandbox=True,
                execution=policy.get("execution"),
                grant_id=policy.get("grant_id"),
                project_types=policy.get("project_types") or [],
                long_task=bool(policy.get("long_task")),
                timeout_sec=policy.get("timeout_sec"),
                output_limit=policy.get("output_limit")
            )

        effective_timeout = int(policy.get("timeout_sec") or timeout_sec or 60)
        output_limit      = int(policy.get("output_limit") or self.max_output_chars)
        env               = os.environ.copy()

        python_runtime = PythonRuntimeResolver.resolve_shell_command(cmd, env=env)
        if not python_runtime.get("ok"):
            return self._ok(
                "shell exec requires cloud sandbox",
                ok=False,
                command=cmd,
                cwd=self._rel(workdir),
                risk=policy.get("risk"),
                category=policy.get("category"),
                risk_reasons=[
                    *(policy.get("reasons") or []),
                    str(python_runtime.get("reason") or "local_python_unavailable")
                ],
                approval_required=bool(policy.get("approval_required")),
                execution_target="cloud_sandbox",
                requires_cloud_sandbox=True,
                execution=policy.get("execution"),
                grant_id=policy.get("grant_id"),
                project_types=policy.get("project_types") or [],
                long_task=bool(policy.get("long_task")),
                timeout_sec=policy.get("timeout_sec"),
                output_limit=policy.get("output_limit"),
                reason=python_runtime.get("reason"),
                python_runtime=python_runtime
            )

        exec_cmd = list(python_runtime.get("command") or cmd)
        if not python_runtime.get("changed"):
            exec_cmd = NativeCommandRuntime.resolve_command(exec_cmd, env=env)

        audit_mode   = self._audit_mode_for_command(cmd, audit_files=audit_files)
        audit_before = self._capture_shell_audit(audit_mode)
        started      = time.perf_counter()

        proc = await Flux.cmd_link_exec(exec_cmd, cwd=str(workdir), env=env)

        timed_out = False

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=max(1, effective_timeout)
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout, stderr = await proc.communicate()

        elapsed_ms  = int((time.perf_counter() - started) * 1000)
        audit_after = self._capture_shell_audit(audit_mode)

        shell_file_changes = self.diff_file_fingerprints(audit_before, audit_after) if audit_mode != "off" else {
            "changed"        : False,
            "change_count"   : 0,
            "created"        : [],
            "modified"       : [],
            "deleted"        : [],
            "created_count"  : 0,
            "modified_count" : 0,
            "deleted_count"  : 0,
            "truncated"      : False
        }

        raw_stdout = self._decode(stdout or b"")
        raw_stderr = self._decode(stderr or b"")
        out_text   = self._clip_output(raw_stdout, max_chars=output_limit)
        err_text   = self._clip_output(raw_stderr, max_chars=output_limit)
        exit_code  = int(proc.returncode or 0)

        ok = (exit_code == 0) and not timed_out

        stdout_truncated = len(raw_stdout) > output_limit
        stderr_truncated = len(raw_stderr) > output_limit

        logger.debug(
            f"native shell exit ok={ok} rc={exit_code} elapsed_ms={elapsed_ms} "
            f"cmd={summarize_command(cmd)}"
        )
        return {
            "text": f"shell exec {'ok' if ok else 'failed'} exit_code={exit_code} elapsed_ms={elapsed_ms}",
            "attachments": [],
            "data": {
                "ok": ok,
                "command": cmd,
                "resolved_command": exec_cmd,
                "cwd": self._rel(workdir),
                "risk": policy.get("risk"),
                "category": policy.get("category"),
                "risk_reasons": policy.get("reasons") or [],
                "approval_required": bool(policy.get("approval_required")),
                "execution_target": policy.get("execution_target"),
                "requires_cloud_sandbox": bool(policy.get("requires_cloud_sandbox")),
                "execution": policy.get("execution"),
                "grant_id": policy.get("grant_id"),
                "python_runtime": python_runtime if python_runtime.get("changed") else None,
                "project_types": policy.get("project_types") or [],
                "long_task": bool(policy.get("long_task")),
                "timeout_sec": effective_timeout,
                "output_limit": output_limit,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "truncated": stdout_truncated or stderr_truncated,
                "recommended_next_steps": self._shell_output_next_steps(
                    cmd=cmd,
                    cwd=self._rel(workdir),
                    timeout_sec=effective_timeout,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated
                ),
                "file_audit_enabled": audit_mode != "off",
                "file_audit_mode": audit_mode,
                "shell_file_changes": shell_file_changes,
                "shell_write_detected": bool(shell_file_changes.get("changed")),
                "exit_code": exit_code,
                "timed_out": timed_out,
                "elapsed_ms": elapsed_ms,
                "stdout": out_text,
                "stderr": err_text
            },
            "logs": []
        }

    def _capture_shell_audit(self, mode: str) -> dict[str, typing.Any] | None:
        """按审计模式采集文件指纹。"""
        if mode == "off":
            return None
        return self.capture_file_fingerprints(hash_files=mode == "full")


if __name__ == '__main__':
    pass
