# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import shutil
import time
import typing
import asyncio
from pathlib import Path
from loguru import logger
from backend.mcp_core.native_coding.base import NativeCodingComponent
from backend.utilities.process import Flux
from backend.utilities.trace import summarize_command


class ShellGitTools(NativeCodingComponent):
    LOCAL_DELETE_DIR_NAMES = {
        "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "htmlcov", "dist", "build"
    }
    LOCAL_DELETE_FILE_NAMES = {
        ".coverage"
    }
    LOCAL_DELETE_SUFFIXES = {
        ".pyc"
    }

    async def shell_exec(
        self,
        *,
        command: list[str],
        cwd: str = ".",
        timeout_sec: int = 60,
        allow_review: bool = False,
        allow_dangerous: bool = False,
        audit_files: bool = True
    ) -> dict[str, typing.Any]:
        cmd = [str(item) for item in (command or []) if str(item or "").strip()]
        if not cmd:
            return self._fail("command_empty")
        policy = self.check_command_policy(
            cmd,
            cwd=cwd,
            timeout_sec=timeout_sec,
            allow_review=allow_review,
            allow_dangerous=allow_dangerous
        )
        if not policy["ok"]:
            return self._fail(
                policy["reason"],
                command=cmd,
                risk=policy.get("risk"),
                category=policy.get("category"),
                reasons=policy.get("reasons") or [],
                approval_required=bool(policy.get("approval_required")),
                project_types=policy.get("project_types") or [],
                execution_target=policy.get("execution_target"),
                requires_cloud_sandbox=bool(policy.get("requires_cloud_sandbox")),
                sandbox_request=policy.get("sandbox_request"),
                outside_sandbox_request=policy.get("outside_sandbox_request")
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
                sandbox_request=policy.get("sandbox_request"),
                outside_sandbox_request=policy.get("outside_sandbox_request"),
                project_types=policy.get("project_types") or [],
                long_task=bool(policy.get("long_task")),
                timeout_sec=policy.get("timeout_sec"),
                output_limit=policy.get("output_limit")
            )

        if allow_dangerous:
            local_delete = self._local_delete_request(cmd, workdir)
            if isinstance(local_delete, dict):
                return self._run_local_delete(
                    request=local_delete,
                    cmd=cmd,
                    workdir=workdir,
                    policy=policy,
                    timeout_sec=timeout_sec
                )

        effective_timeout = int(policy.get("timeout_sec") or timeout_sec or 60)
        output_limit = int(policy.get("output_limit") or self.max_output_chars)
        audit_before = self.capture_file_fingerprints() if audit_files else None
        started = time.perf_counter()
        proc = await Flux.cmd_link_exec_resolved(cmd, cwd=str(workdir), env=os.environ.copy())
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

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        audit_after = self.capture_file_fingerprints() if audit_files else None
        shell_file_changes = self.diff_file_fingerprints(audit_before, audit_after) if audit_files else {
            "changed": False,
            "change_count": 0,
            "created": [],
            "modified": [],
            "deleted": [],
            "created_count": 0,
            "modified_count": 0,
            "deleted_count": 0,
            "truncated": False
        }
        raw_stdout = self._decode(stdout or b"")
        raw_stderr = self._decode(stderr or b"")
        out_text = self._clip_output(raw_stdout, max_chars=output_limit)
        err_text = self._clip_output(raw_stderr, max_chars=output_limit)
        exit_code = int(proc.returncode or 0)
        ok = (exit_code == 0) and not timed_out

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
                "cwd": self._rel(workdir),
                "risk": policy.get("risk"),
                "category": policy.get("category"),
                "risk_reasons": policy.get("reasons") or [],
                "approval_required": bool(policy.get("approval_required")),
                "execution_target": policy.get("execution_target"),
                "requires_cloud_sandbox": bool(policy.get("requires_cloud_sandbox")),
                "sandbox_request": policy.get("sandbox_request"),
                "outside_sandbox_request": policy.get("outside_sandbox_request"),
                "project_types": policy.get("project_types") or [],
                "long_task": bool(policy.get("long_task")),
                "timeout_sec": effective_timeout,
                "output_limit": output_limit,
                "stdout_truncated": len(raw_stdout) > output_limit,
                "stderr_truncated": len(raw_stderr) > output_limit,
                "file_audit_enabled": bool(audit_files),
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

    def _local_delete_request(
        self,
        cmd: list[str],
        workdir: Path
    ) -> dict[str, typing.Any] | None:
        """识别已审批且可在工作区内处理的缓存/构建产物删除命令。"""
        if not cmd:
            return None
        head = Path(str(cmd[0])).name.lower()
        if head not in {"rm", "rmdir", "del", "erase", "remove-item", "ri", "rd"}:
            return None

        recursive = False
        targets: list[Path] = []
        for raw in cmd[1:]:
            item = str(raw or "").strip()
            if not item:
                continue
            lower = item.lower()
            if lower in {"-r", "-rf", "-fr", "--recursive", "/s", "-recurse"}:
                recursive = True
                continue
            if lower in {"-f", "--force", "/q", "-force"}:
                continue
            if lower.startswith("-") or "*" in item or "?" in item:
                return None
            target = Path(item)
            if not target.is_absolute():
                target = workdir / target
            resolved = target.resolve()
            if resolved != self.root and self.root not in resolved.parents:
                return None
            try:
                rel_parts = resolved.relative_to(self.root).parts
            except ValueError:
                return None
            if resolved == self.root or ".git" in rel_parts:
                return None
            if not self._local_delete_target_allowed(resolved):
                return None
            targets.append(resolved)

        if not targets:
            return None
        return {
            "targets": targets,
            "recursive": recursive
        }

    def _local_delete_target_allowed(self, target: Path) -> bool:
        name = target.name
        lower_name = name.lower()
        if lower_name in self.LOCAL_DELETE_DIR_NAMES:
            return True
        if name in self.LOCAL_DELETE_FILE_NAMES:
            return True
        if any(lower_name.endswith(suffix) for suffix in self.LOCAL_DELETE_SUFFIXES):
            return True
        return False

    def _run_local_delete(
        self,
        *,
        request: dict[str, typing.Any],
        cmd: list[str],
        workdir: Path,
        policy: dict[str, typing.Any],
        timeout_sec: int
    ) -> dict[str, typing.Any]:
        output_limit = int(policy.get("output_limit") or self.max_output_chars)
        started = time.perf_counter()
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        exit_code = 0
        audit_before = self.capture_file_fingerprints()

        for target in request.get("targets") or []:
            path = Path(target)
            rel = self._rel(path)
            try:
                if not path.exists():
                    stderr_lines.append(f"missing: {rel}")
                    exit_code = 1
                    continue
                if path.is_dir():
                    if not request.get("recursive"):
                        stderr_lines.append(f"is a directory: {rel}")
                        exit_code = 1
                        continue
                    shutil.rmtree(path)
                    stdout_lines.append(f"deleted directory: {rel}")
                    continue
                path.unlink()
                stdout_lines.append(f"deleted file: {rel}")
            except OSError as exc:
                stderr_lines.append(f"{rel}: {type(exc).__name__}: {exc}")
                exit_code = 1

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        audit_after = self.capture_file_fingerprints()
        shell_file_changes = self.diff_file_fingerprints(audit_before, audit_after)
        raw_stdout = "\n".join(stdout_lines)
        raw_stderr = "\n".join(stderr_lines)
        ok = exit_code == 0
        return {
            "text": f"shell exec {'ok' if ok else 'failed'} exit_code={exit_code} elapsed_ms={elapsed_ms}",
            "attachments": [],
            "data": {
                "ok": ok,
                "command": cmd,
                "cwd": self._rel(workdir),
                "risk": policy.get("risk"),
                "category": policy.get("category"),
                "risk_reasons": policy.get("reasons") or [],
                "approval_required": bool(policy.get("approval_required")),
                "execution_target": policy.get("execution_target"),
                "requires_cloud_sandbox": bool(policy.get("requires_cloud_sandbox")),
                "sandbox_request": policy.get("sandbox_request"),
                "outside_sandbox_request": policy.get("outside_sandbox_request"),
                "project_types": policy.get("project_types") or [],
                "long_task": bool(policy.get("long_task")),
                "timeout_sec": int(policy.get("timeout_sec") or timeout_sec or 60),
                "output_limit": output_limit,
                "stdout_truncated": len(raw_stdout) > output_limit,
                "stderr_truncated": len(raw_stderr) > output_limit,
                "file_audit_enabled": True,
                "shell_file_changes": shell_file_changes,
                "shell_write_detected": bool(shell_file_changes.get("changed")),
                "exit_code": exit_code,
                "timed_out": False,
                "elapsed_ms": elapsed_ms,
                "stdout": self._clip_output(raw_stdout, max_chars=output_limit),
                "stderr": self._clip_output(raw_stderr, max_chars=output_limit)
            },
            "logs": []
        }

    async def git_status(self) -> dict[str, typing.Any]:
        if not self._is_git_workspace():
            return self._ok(
                "git status unavailable: workspace is not a git repository",
                stdout="",
                available=False
            )
        return await self._git(["status", "--short"])

    async def git_diff(self, path: str | None = None, max_chars: int = 24000) -> dict[str, typing.Any]:
        if not self._is_git_workspace():
            return self._ok(
                "git diff unavailable: workspace is not a git repository",
                stdout="",
                available=False
            )
        cmd = ["diff", "--"]
        if path:
            cmd.append(self._rel(self._resolve(path)))
        result = await self._git(cmd)
        data = result.get("data") or {}
        data["stdout"] = self._clip_output(str(data.get("stdout") or ""), max_chars=max_chars)
        return result

    async def _git(self, args: list[str]) -> dict[str, typing.Any]:
        return await self.shell_exec(
            command=["git", *args],
            cwd=".",
            timeout_sec=60,
            allow_review=True,
            allow_dangerous=True,
            audit_files=False
        )

    def _is_git_workspace(self) -> bool:
        return (self.root / ".git").exists()


if __name__ == '__main__':
    pass
