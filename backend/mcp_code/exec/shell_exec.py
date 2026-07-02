# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import time
import shlex
import typing
import asyncio
from loguru import logger
from backend.mcp_code.base import (
    NativeCodingBase, NativeCodingComponent
)
from backend.mcp_code.exec.shell_runtime import ShellRuntimeResolver
from backend.utilities.process import Flux
from backend.utilities.trace import summarize_command


class ShellCommandTools(NativeCodingComponent):
    """提供受控 shell 执行能力。"""

    AUDIT_METADATA_COMMANDS = {
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

    AUDIT_METADATA_PYTHON_FLAGS = {
        "-c",
        "-m",
        "--version",
        "-V"
    }

    AUDIT_METADATA_VERSION_COMMANDS = {
        "go"     : {"version"},
        "node"   : {"--version", "-v"},
        "npm"    : {"--version", "-v", "version"},
        "npx"    : {"--version", "-v"},
        "java"   : {"-version", "--version"},
        "javac"  : {"-version", "--version"},
        "mvn"    : {"-version", "--version", "-v"},
        "gradle" : {"-version", "--version", "-v"}
    }

    AUDIT_METADATA_GIT_SUBCOMMANDS = {
        "branch",
        "diff",
        "log",
        "rev-parse",
        "show",
        "status"
    }

    def __init__(
        self,
        core: NativeCodingBase,
        *,
        command_policy: typing.Any,
        file_audit: typing.Any
    ) -> None:
        """保存共享运行时上下文、命令策略和文件审计依赖。"""
        super().__init__(core)
        self._command_policy = command_policy
        self._file_audit     = file_audit

    @classmethod
    def audit_mode_for_command(
        cls,
        command: str,
        *,
        audit_files: bool
    ) -> str:
        """根据命令类型选择审计强度。"""
        if not audit_files:
            return "off"
        text = str(command or "").strip()
        if not text:
            return "full"

        if any(marker in text for marker in ("|", ">", "<", ";", "&", "`", "$(", "\n")):
            return "full"

        parts = cls._split_command(text)
        if not parts:
            return "full"

        executable = os.path.basename(parts[0]).lower()
        lowered    = text.lower()

        if executable == "git":
            subcommand = parts[1].lower() if len(parts) > 1 else ""
            return "metadata" if subcommand in cls.AUDIT_METADATA_GIT_SUBCOMMANDS else "full"

        if executable in {"python", "python3", "py"}:
            if cls._python_read_only(text):
                return "metadata"
            return "full"

        version_flags = cls.AUDIT_METADATA_VERSION_COMMANDS.get(executable)
        if version_flags:
            if cls._version_command_matches(lowered, version_flags):
                return "metadata"
            return "full"

        if executable in cls.AUDIT_METADATA_COMMANDS:
            return "metadata"

        return "full"

    @staticmethod
    def _split_command(command: str) -> list[str]:
        """尽量按 shell 词法切分命令头，用于审计分类。"""
        try:
            parts = shlex.split(command, posix=True)
        except ValueError:
            parts = str(command or "").strip().split()
        return [str(item) for item in parts if str(item or "").strip()]

    @classmethod
    def _python_read_only(cls, command: str) -> bool:
        text = str(command or "").strip()
        if not text:
            return False
        parts = cls._split_command(text)
        if len(parts) < 2:
            return False
        return parts[1] in cls.AUDIT_METADATA_PYTHON_FLAGS or parts[1].lower() in cls.AUDIT_METADATA_PYTHON_FLAGS

    @classmethod
    def _version_command_matches(cls, text: str, flags: set[str]) -> bool:
        parts = cls._split_command(text)
        if not parts:
            return False
        normalized_args = {str(item).lower() for item in parts[1:]}
        if not normalized_args:
            return False
        return normalized_args.issubset(flags)

    def _capture_shell_audit(self, mode: str) -> dict[str, typing.Any] | None:
        """按审计模式采集文件指纹。"""
        if mode == "off":
            return None
        return self._file_audit.capture_file_fingerprints(hash_files=mode == "full")

    def _record_shell_result(self, data: dict[str, typing.Any]) -> None:
        """记录最近一次 shell_command 结果，供后续质量检查使用。"""
        if not isinstance(data, dict):
            return
        record = dict(data)

        self.core.last_shell_result = record

        history = getattr(self.core, "validation_history", None)
        if not isinstance(history, list):
            history = []
            self.core.validation_history = history
        history.append(record)
        del history[:-20]

    async def shell_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout_sec: int = 60,
        execution: dict[str, typing.Any] | None = None,
        audit_files: bool = True
    ) -> dict[str, typing.Any]:
        """按执行元数据运行 shell 命令，必要时返回云端沙盒交接结果。"""
        cmd = str(command or "").strip()
        if not cmd:
            return self.fail_result("command_empty")

        policy = self._command_policy.execution_metadata_policy(
            execution,
            command=cmd,
            cwd=cwd,
            timeout_sec=timeout_sec
        )

        if not policy["ok"]:
            data = {
                "command"                : cmd,
                "risk"                   : policy.get("risk"),
                "category"               : policy.get("category"),
                "risk_signals"           : policy.get("reasons") or [],
                "approval_required"      : bool(policy.get("approval_required")),
                "project_types"          : policy.get("project_types") or [],
                "execution_target"       : policy.get("execution_target"),
                "requires_cloud_sandbox" : bool(policy.get("requires_cloud_sandbox")),
                "execution"              : policy.get("execution"),
                "grant_id"               : policy.get("grant_id"),
                "error"                  : "execution_policy_blocked"
            }
            result = {
                "ok"          : False,
                "text"        : "shell_command blocked by execution policy",
                "attachments" : [],
                "data"        : data,
                "logs"        : []
            }

            self._record_shell_result(data)
            return result

        workdir = self.resolve_path(cwd)
        if not workdir.is_dir():
            data = {
                "cwd"     : cwd,
                "command" : cmd,
                "error"   : "cwd_not_directory"
            }
            result = {
                "ok"          : False,
                "text"        : "shell_command cwd is not a directory",
                "attachments" : [],
                "data"        : data,
                "logs"        : []
            }
            self._record_shell_result(data)
            return result

        if policy.get("execution_target") == "cloud_sandbox":
            result = self.ok_result(
                "shell_command requires cloud sandbox",
                ok=False,
                command=cmd,
                cwd=self.relative_path(workdir),
                risk=policy.get("risk"),
                category=policy.get("category"),
                risk_signals=policy.get("reasons") or [],
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
            self._record_shell_result(result.get("data") or {})
            return result

        effective_timeout = int(policy.get("timeout_sec") or timeout_sec or 60)
        output_limit      = int(policy.get("output_limit") or self.max_output_chars)
        env               = os.environ.copy()

        runtime = ShellRuntimeResolver.resolve(env=env)

        runtime_info = {
            "name"       : runtime.name,
            "syntax"     : runtime.syntax,
            "executable" : runtime.executable,
            "source"     : runtime.source
        }

        exec_cmd = list(runtime.prefix or [])
        exec_cmd.append(cmd)

        audit_mode   = self.audit_mode_for_command(cmd, audit_files=audit_files)
        audit_before = self._capture_shell_audit(audit_mode)
        started      = time.perf_counter()

        proc = await Flux.cmd_link_shell_exec(
            cmd,
            shell=runtime.prefix or None,
            cwd=str(workdir),
            env=env
        )

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

        shell_file_changes = self._file_audit.diff_file_fingerprints(
            audit_before, audit_after
        ) if audit_mode != "off" else {
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

        raw_stdout = self.decode_bytes(stdout or b"")
        raw_stderr = self.decode_bytes(stderr or b"")
        out_text   = self.clip_output(raw_stdout, max_chars=output_limit)
        err_text   = self.clip_output(raw_stderr, max_chars=output_limit)
        exit_code  = int(proc.returncode or 0)

        ok = (exit_code == 0) and not timed_out

        stdout_truncated = len(raw_stdout) > output_limit
        stderr_truncated = len(raw_stderr) > output_limit

        logger.debug(
            f"native shell exit ok={ok} rc={exit_code} elapsed_ms={elapsed_ms} "
            f"cmd={summarize_command(cmd)}"
        )

        data = {
            "command"                : cmd,
            "resolved_command"       : exec_cmd,
            "cwd"                    : self.relative_path(workdir),
            "risk"                   : policy.get("risk"),
            "category"               : policy.get("category"),
            "risk_signals"           : policy.get("reasons") or [],
            "approval_required"      : bool(policy.get("approval_required")),
            "execution_target"       : policy.get("execution_target"),
            "requires_cloud_sandbox" : bool(policy.get("requires_cloud_sandbox")),
            "execution"              : policy.get("execution"),
            "grant_id"               : policy.get("grant_id"),
            "runtime"                : runtime_info,
            "runtime_name"           : runtime.name,
            "project_types"          : policy.get("project_types") or [],
            "long_task"              : bool(policy.get("long_task")),
            "timeout_sec"            : effective_timeout,
            "output_limit"           : output_limit,
            "stdout_truncated"       : stdout_truncated,
            "stderr_truncated"       : stderr_truncated,
            "truncated"              : stdout_truncated or stderr_truncated,
            "file_audit_enabled"     : audit_mode != "off",
            "file_audit_mode"        : audit_mode,
            "shell_file_changes"     : shell_file_changes,
            "shell_write_detected"   : bool(shell_file_changes.get("changed")),
            "exit_code"              : exit_code,
            "timed_out"              : timed_out,
            "elapsed_ms"             : elapsed_ms,
            "stdout"                 : out_text,
            "stderr"                 : err_text
        }

        if not ok:
            data["reason"] = "command_timed_out" if timed_out else "command_failed"
            self.core.enrich_failure_facts(data)
        self._record_shell_result(data)

        return {
            "ok"          : ok,
            "text"        : f"shell_command {'ok' if ok else 'failed'} exit_code={exit_code} elapsed_ms={elapsed_ms}",
            "attachments" : [],
            "data"        : data,
            "logs"        : []
        }


if __name__ == '__main__':
    pass
