# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import time
import typing
import asyncio
import subprocess
from backend.mcp_core.coding_native.base import NativeCodingComponent
from backend.mcp_core.coding_native.command_runtime import NativeCommandRuntime
from backend.utilities.process import Flux


class GitTools(NativeCodingComponent):
    """提供 git 状态和 diff 能力。"""

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

    async def _git(
        self,
        args: list[str],
        *,
        output_limit: int | None = None
    ) -> dict[str, typing.Any]:
        """在工作区根目录执行 git 子命令并返回统一结果。"""
        cmd     = ["git", *args]
        workdir = self._resolve(".")
        env     = os.environ.copy()
        limit   = max(1, min(int(output_limit or self.max_output_chars), self.max_output_chars))
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

        data = {
            "ok"                     : ok,
            "command"                : cmd,
            "cwd"                    : self._rel(workdir),
            "execution_target"       : "local",
            "requires_cloud_sandbox" : False,
            "exit_code"              : exit_code,
            "timed_out"              : timed_out,
            "elapsed_ms"             : elapsed_ms,
            "output_limit"           : limit,
            "stdout"                 : self._clip_output(raw_stdout, max_chars=limit),
            "stderr"                 : self._clip_output(raw_stderr, max_chars=limit),
            "stdout_truncated"       : len(raw_stdout) > limit,
            "stderr_truncated"       : len(raw_stderr) > limit,
            "truncated"              : len(raw_stdout) > limit or len(raw_stderr) > limit
        }

        return {
            "text"        : f"git {'ok' if ok else 'failed'} exit_code={exit_code} elapsed_ms={elapsed_ms}",
            "attachments" : [],
            "data"        : data,
            "logs"        : []
        }

    async def git_status(
        self
    ) -> dict[str, typing.Any]:
        """返回当前工作区的 git status 摘要。"""
        if not self._is_git_workspace():
            return self._ok(
                "git status unavailable: workspace is not a git repository",
                stdout="",
                available=False
            )
        return await self._git(["status", "--short"])

    async def git_diff(
        self,
        path: str | None = None,
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

        output_limit = max(1, min(int(max_chars or self.max_output_chars), self.max_output_chars))
        result       = await self._git(cmd, output_limit=output_limit)
        data         = result.get("data") or {}

        if data.get("stdout_truncated") and output_limit < self.max_output_chars:
            data["recommended_next_steps"] = [
                {
                    "tool": "git_diff",
                    "args": {
                        "path"      : path,
                        "max_chars" : min(output_limit * 2, self.max_output_chars)
                    },
                    "reason": "increase_limit"
                }
            ]
        return result


if __name__ == '__main__':
    pass
