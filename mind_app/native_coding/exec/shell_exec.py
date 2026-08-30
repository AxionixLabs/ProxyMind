# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import shlex
import time
import typing
from observability import observe
from mind_app.native_coding.base import (
    NativeCodingBase,
    NativeCodingComponent
)
from mind_app.native_coding.encoding import normalize_process_output_encoding
from mind_app.native_coding.exec.output_decoder import CapturedOutputDecoder
from mind_app.native_coding.exec.process_capture import ProcessCapture
from mind_app.native_coding.exec.process_capture import (
    CapturedOutputLine,
    CapturedProcessResult
)
from mind_app.native_coding.exec.process_session import (
    ProcessSessionManager,
    ProcessSessionSpec
)
from mind_app.native_coding.exec.sandbox_client import (
    SandboxProtocolError,
    SandboxUnavailable,
    sandbox_backend_name,
)
from mind_app.native_coding.exec.shell_runtime import ShellRuntimeResolver
from mind_app.native_coding.exec.exec_policy import (
    effective_sandbox_mode,
    normalize_sandbox_permission,
)
from agent.stores.permission_grants import normalize_permission_profile
from mind_app.runtime.processes import wait_for_process


class ShellCommandTools(NativeCodingComponent):
    """提供受控 shell 执行能力。"""

    OUTPUT_LINE_MAX_CHARS = 1000

    AUDIT_METADATA_COMMANDS = {
        "cat",
        "dir",
        "echo",
        "find",
        "gc",
        "gci",
        "get-childitem",
        "get-content",
        "git",
        "grep",
        "head",
        "ls",
        "python",
        "python3",
        "py",
        "select-string",
        "sls",
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
        file_audit: typing.Any,
        sessions: ProcessSessionManager
    ) -> None:
        """保存共享运行时上下文、命令策略和文件审计依赖。"""
        super().__init__(core)
        self._command_policy = command_policy
        self._file_audit     = file_audit
        self._sessions       = sessions

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
        output_encoding: str = "auto",
        audit_files: bool = False,
        sandbox_mode: str = "danger-full-access",
        sandbox_permissions: object = "use_default",
        additional_permissions: dict[str, typing.Any] | None = None,
    ) -> dict[str, typing.Any]:
        """按执行元数据运行 shell 命令，必要时返回云端沙盒交接结果。"""
        cmd = str(command or "")
        if not cmd.strip():
            return self.fail_result("command_empty")
        try:
            permission = normalize_sandbox_permission(sandbox_permissions)
            sandbox_mode = effective_sandbox_mode(sandbox_mode, permission)
        except ValueError as exc:
            return self.fail_result(
                "sandbox_permissions_invalid",
                command=cmd,
                detail=str(exc),
            )
        if permission == "with_additional_permissions" and additional_permissions is None:
            return self.fail_result(
                "additional_permissions_required",
                command=cmd,
            )
        if permission != "with_additional_permissions" and additional_permissions is not None:
            return self.fail_result(
                "additional_permissions_unexpected",
                command=cmd,
            )
        if sandbox_mode not in {
            "danger-full-access",
            "read-only",
            "workspace-read",
            "workspace-write",
        }:
            return self.fail_result(
                "sandbox_mode_invalid",
                command=cmd,
                sandbox_mode=sandbox_mode,
            )

        try:
            normalized_output_encoding = normalize_process_output_encoding(output_encoding)
        except ValueError:
            result = self.fail_result(
                "output_encoding_invalid",
                command=cmd,
                output_encoding=str(output_encoding or "")
            )
            self._record_shell_result(result.get("data") or {})
            return result

        policy = self._command_policy.local_command_policy(
            command=cmd,
            cwd=cwd,
            timeout_sec=timeout_sec,
            tool="shell_command",
            arguments={
                "command": cmd,
                "cwd": str(cwd or "."),
                "timeout_sec": int(timeout_sec or 60),
                "output_encoding": normalized_output_encoding,
            },
        )

        if not policy["ok"]:
            data = {
                "command": cmd,
                "risk": policy.get("risk"),
                "category": policy.get("category"),
                "risk_signals": policy.get("reasons") or [],
                "project_types": policy.get("project_types") or [],
                "execution_target": policy.get("execution_target"),
                "error": "execution_policy_blocked"
            }
            result = {
                "ok": False,
                "text": "shell_command blocked by execution policy",
                "attachments": [],
                "data": data,
                "logs": []
            }

            self._record_shell_result(data)
            return result

        workdir = self.resolve_path(cwd)
        if not workdir.is_dir():
            data = {
                "cwd": cwd,
                "command": cmd,
                "error": "cwd_not_directory"
            }
            result = {
                "ok": False,
                "text": "shell_command cwd is not a directory",
                "attachments": [],
                "data": data,
                "logs": []
            }
            self._record_shell_result(data)
            return result

        normalized_additional_permissions: dict[str, typing.Any] | None = None
        if additional_permissions is not None:
            try:
                normalized_additional_permissions = normalize_permission_profile(
                    additional_permissions,
                    cwd=workdir,
                )
            except ValueError as exc:
                return self.fail_result(
                    "additional_permissions_invalid",
                    command=cmd,
                    detail=str(exc),
                )

        effective_timeout = int(policy.get("timeout_sec") or timeout_sec or 60)
        output_limit      = int(policy.get("output_limit") or self.max_output_chars)

        env     = os.environ.copy()
        runtime = ShellRuntimeResolver.resolve(env=env)

        runtime_info = {
            "name": runtime.name,
            "syntax": runtime.syntax,
            "executable": runtime.executable,
            "source": runtime.source,
            "sandbox_mode": sandbox_mode,
            "sandbox_permissions": permission,
        }
        if normalized_additional_permissions is not None:
            runtime_info["additional_permissions"] = normalized_additional_permissions

        exec_cmd = list(runtime.prefix or [])
        exec_cmd.append(cmd)

        audit_mode   = self.audit_mode_for_command(cmd, audit_files=audit_files)
        audit_before = self._capture_shell_audit(audit_mode)

        try:
            if sandbox_mode in {"read-only", "workspace-read", "workspace-write"}:
                capture = await self._run_sandbox_capture(
                    command=cmd,
                    args=tuple(exec_cmd),
                    cwd=str(workdir),
                    env=env,
                    timeout_sec=effective_timeout,
                    sandbox_mode=sandbox_mode,
                    additional_permissions=normalized_additional_permissions,
                )
            else:
                capture = await ProcessCapture.run_shell(
                    cmd,
                    shell=runtime.prefix or None,
                    cwd=str(workdir),
                    env=env,
                    timeout_sec=effective_timeout,
                    buffer_limit_bytes=max(output_limit * 2, output_limit + 4096)
                )
        except (
            SandboxUnavailable,
            SandboxProtocolError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            data = {
                "command": cmd,
                "cwd": self.relative_path(workdir),
                "sandbox_mode": sandbox_mode,
                "sandbox_permissions": permission,
                "execution_backend": sandbox_backend_name(),
                "error": "sandbox_unavailable",
                "detail": str(exc).strip() or type(exc).__name__,
            }
            result = {
                "ok": False,
                "text": "shell_command sandbox unavailable",
                "attachments": [],
                "data": data,
                "logs": [],
            }
            self._record_shell_result(data)
            return result

        elapsed_ms  = capture.elapsed_ms
        audit_after = self._capture_shell_audit(audit_mode)

        shell_file_changes = self._file_audit.diff_file_fingerprints(
            audit_before, audit_after
        ) if audit_mode != "off" else {
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

        decoded_output = CapturedOutputDecoder(
            encoding=normalized_output_encoding,
            line_limit=self.OUTPUT_LINE_MAX_CHARS,
        ).decode(capture)
        detected_output_encodings_by_stream = {
            stream: list(encodings)
            for stream, encodings in decoded_output.stream_encodings.items()
        }

        raw_stdout = decoded_output.stdout
        raw_stderr = decoded_output.stderr
        out_text   = self.clip_output(raw_stdout, max_chars=output_limit)
        err_text   = self.clip_output(raw_stderr, max_chars=output_limit)
        exit_code  = int(capture.exit_code or 0)

        ok = (exit_code == 0) and not capture.timed_out

        stdout_truncated = capture.stdout_dropped > 0 or len(raw_stdout) > output_limit
        stderr_truncated = capture.stderr_dropped > 0 or len(raw_stderr) > output_limit

        observe(
            "native_shell.complete",
            ok=ok,
            exit_code=exit_code,
            elapsed_ms=elapsed_ms,
            timed_out=capture.timed_out,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

        data = {
            "command": cmd,
            "resolved_command": exec_cmd,
            "cwd": self.relative_path(workdir),
            "risk": policy.get("risk"),
            "category": policy.get("category"),
            "risk_signals": policy.get("reasons") or [],
            "execution_target": policy.get("execution_target"),
            "runtime": runtime_info,
            "runtime_name": runtime.name,
            "sandbox_mode": sandbox_mode,
            "sandbox_permissions": permission,
            "execution_backend": (
                sandbox_backend_name()
                if sandbox_mode in {"read-only", "workspace-read", "workspace-write"}
                else "local"
            ),
            "project_types": policy.get("project_types") or [],
            "long_task": bool(policy.get("long_task")),
            "timeout_sec": effective_timeout,
            "output_limit": output_limit,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "truncated": stdout_truncated or stderr_truncated,
            "file_audit_enabled": audit_mode != "off",
            "file_audit_mode": audit_mode,
            "shell_file_changes": shell_file_changes,
            "shell_write_detected": bool(shell_file_changes.get("changed")),
            "exit_code": exit_code,
            "timed_out": capture.timed_out,
            "elapsed_ms": elapsed_ms,
            "output_encoding": normalized_output_encoding,
            "detected_output_encodings": list(decoded_output.encodings),
            "detected_output_encodings_by_stream": detected_output_encodings_by_stream,
            "output_encoding_ambiguous": decoded_output.ambiguous,
            "stdout": out_text,
            "stderr": err_text,
            "output_lines": list(decoded_output.output_lines)
        }

        if not ok:
            data["reason"] = "command_timed_out" if capture.timed_out else "command_failed"
            self.core.enrich_failure_facts(data)

        self._record_shell_result(data)

        return {
            "ok": ok,
            "text": f"shell_command {'ok' if ok else 'failed'} exit_code={exit_code} elapsed_ms={elapsed_ms}",
            "attachments": [],
            "data": data,
            "logs": []
        }

    async def _run_sandbox_capture(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: str,
        env: dict[str, str],
        timeout_sec: int,
        sandbox_mode: str,
        additional_permissions: dict[str, typing.Any] | None,
    ) -> CapturedProcessResult:
        """通过当前平台 sidecar 执行一次命令并转换为统一捕获结果。"""
        started = time.perf_counter()
        session = await self._sessions.start(ProcessSessionSpec(
            command=command,
            args=args,
            cwd=cwd,
            display_cwd=self.relative_path(self.resolve_path(cwd)),
            runtime={
                "name": sandbox_backend_name(),
                "source": sandbox_backend_name(),
            },
            origin="tool",
            timeout_sec=max(1, int(timeout_sec)),
            idle_timeout_sec=max(1, int(timeout_sec)),
            stdin_enabled=False,
            env=env,
            sandbox_mode=sandbox_mode,
            additional_permissions=additional_permissions,
        ))

        timed_out = False
        try:
            await wait_for_process(session.process, max(1, int(timeout_sec)) * 1000)
            if session.process.returncode is None:
                timed_out = True
                await self._sessions.apply(session, control="kill")
                await wait_for_process(session.process, 1000)

            await self._sessions.finalize_if_exited(session)

            async with session.lock:
                stdout = bytes(session.stdout)
                stderr = bytes(session.stderr)
                stdout_dropped = session.stdout_dropped
                stderr_dropped = session.stderr_dropped

                output_records = tuple(
                    CapturedOutputLine(stream=stream, data=bytes(chunk))
                    for _, stream, chunk in session.output_events
                )

            exit_code = session.process.returncode
            if exit_code is None:
                exit_code = -1
            return CapturedProcessResult(
                exit_code=int(exit_code),
                stdout=stdout,
                stderr=stderr,
                output_records=output_records,
                stdout_dropped=stdout_dropped,
                stderr_dropped=stderr_dropped,
                timed_out=timed_out,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
        finally:
            self._sessions.remove(session.session_id)


if __name__ == '__main__':
    pass
