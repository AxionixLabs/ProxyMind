# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import time
import typing

from agent.ports import CapabilityError
from agent.ports import TerminalSize
from agent.ports.process_tools import EXEC_COMMAND_DEFAULT_YIELD_MS
from agent.ports.process_tools import EXEC_COMMAND_MAX_YIELD_MS
from agent.ports.process_tools import EXEC_COMMAND_MIN_YIELD_MS
from agent.ports.process_tools import EXEC_COMMAND_WINDOWS_MIN_YIELD_MS
from agent.ports.process_tools import WRITE_STDIN_DEFAULT_WAIT_MS
from agent.ports.process_tools import WRITE_STDIN_EMPTY_MAX_WAIT_MS
from agent.ports.process_tools import WRITE_STDIN_EMPTY_MIN_WAIT_MS
from agent.ports.process_tools import WRITE_STDIN_MAX_WAIT_MS
from agent.ports.process_tools import WRITE_STDIN_MIN_WAIT_MS
from agent.domain.execution_policy import (
    effective_sandbox_mode,
    normalize_sandbox_permission,
)
from agent.domain.permission_profiles import normalize_permission_profile
from infrastructure.platform.process_sessions import (
    ProcessSession as ExecSession,
    ProcessSessionManager,
    ProcessSessionSpec,
)
from infrastructure.platform.processes import (
    wait_for_process,
)
from infrastructure.platform.sandbox import (
    SandboxError,
    SidecarProcess,
    sandbox_backend_name,
)
from infrastructure.platform.shell_runtime import ShellRuntimeResolver
from infrastructure.workspace.commands.audit import WorkspaceFileAudit
from infrastructure.workspace.commands.profile import CommandExecutionProfile
from infrastructure.workspace.commands.sandbox_failures import map_sandbox_failure
from infrastructure.workspace.commands.shell import ShellCommandExecutor
from infrastructure.workspace.context import (
    WorkspaceComponent,
    WorkspaceContext,
)
from observability import observe_exception


class ProcessCommandExecutor(WorkspaceComponent):
    """提供可持续读写的 shell 会话工具。"""

    def __init__(
        self,
        core: WorkspaceContext,
        *,
        command_policy: CommandExecutionProfile,
        file_audit: WorkspaceFileAudit,
        sessions: ProcessSessionManager
    ) -> None:
        """保存共享运行时、执行策略和会话表。"""
        super().__init__(core)
        self._command_policy = command_policy
        self._file_audit = file_audit
        self._session_manager = sessions
        self._sessions = sessions.sessions

    async def exec_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        shell: str | None = None,
        tty: bool = False,
        terminal_rows: int = 24,
        terminal_columns: int = 80,
        yield_time_ms: int = EXEC_COMMAND_DEFAULT_YIELD_MS,
        max_output_chars: int = 24000,
        timeout_sec: int = 1800,
        idle_timeout_sec: int = 300,
        cid: str = "",
        sid: str = "",
        run_id: str = "",
        environment_id: str = "",
        audit_files: bool = False,
        sandbox_mode: str = "danger-full-access",
        sandbox_permissions: object = "use_default",
        additional_permissions: dict[str, typing.Any] | None = None,
    ) -> dict[str, typing.Any]:
        """启动一个可持续读取和写入的 shell 命令会话。"""
        await self._session_manager.cleanup()

        cmd = str(command or "")
        if not cmd.strip():
            return self.fail_result("command_empty")
        if not isinstance(tty, bool):
            return self.fail_result(
                "tty_invalid",
                tool="exec_command",
                command=cmd,
            )
        try:
            terminal_size = TerminalSize(
                rows=terminal_rows,
                columns=terminal_columns,
            )
        except (TypeError, ValueError) as exc:
            return self.fail_result(
                "terminal_size_invalid",
                tool="exec_command",
                command=cmd,
                detail=str(exc),
            )
        yield_ms = self._resolve_exec_yield_time(yield_time_ms)

        try:
            permission = normalize_sandbox_permission(sandbox_permissions)
            sandbox_mode = effective_sandbox_mode(sandbox_mode, permission)
        except ValueError as exc:
            return self.fail_result(
                "sandbox_permissions_invalid",
                tool="exec_command",
                command=cmd,
                detail=str(exc),
            )
        if permission == "with_additional_permissions" and additional_permissions is None:
            return self.fail_result(
                "additional_permissions_required",
                tool="exec_command",
                command=cmd,
            )
        if permission != "with_additional_permissions" and additional_permissions is not None:
            return self.fail_result(
                "additional_permissions_unexpected",
                tool="exec_command",
                command=cmd,
            )

        policy = self._command_policy.local_command_policy(
            command=cmd,
            cwd=cwd,
            timeout_sec=timeout_sec,
            tool="exec_command",
            arguments={
                "command": cmd,
                "cwd": str(cwd or "."),
                "tty": tty,
                "terminal_rows": terminal_size.rows,
                "terminal_columns": terminal_size.columns,
                "yield_time_ms": yield_ms,
                "max_output_chars": int(max_output_chars),
                "timeout_sec": int(timeout_sec or 1800),
                "idle_timeout_sec": int(idle_timeout_sec),
            },
        )
        if not policy["ok"]:
            return self._policy_blocked_result("exec_command", cmd, cwd, policy)

        workdir = self.resolve_path(cwd)
        if not workdir.is_dir():
            return self.fail_result(
                "cwd_not_directory",
                tool="exec_command",
                command=cmd,
                cwd=cwd
            )

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
                    tool="exec_command",
                    command=cmd,
                    detail=str(exc),
                )

        timeout = self._bounded_int(
            policy.get("timeout_sec"), default=timeout_sec, minimum=1, maximum=7200
        )
        idle_timeout = self._bounded_int(
            idle_timeout_sec, default=300, minimum=1, maximum=1800
        )
        output_limit = self._bounded_int(
            max_output_chars, default=self.max_output_chars, minimum=1024, maximum=120000
        )

        env = os.environ.copy()
        runtime = ShellRuntimeResolver.resolve(env=env, shell=shell)

        runtime_info = {
            "name": runtime.name,
            "syntax": runtime.syntax,
            "executable": runtime.executable,
            "source": runtime.source,
            "sandbox_mode": sandbox_mode,
            "sandbox_permissions": permission,
            "tty": tty,
            "terminal_rows": terminal_size.rows,
            "terminal_columns": terminal_size.columns,
        }
        if normalized_additional_permissions is not None:
            runtime_info["additional_permissions"] = normalized_additional_permissions

        exec_cmd = list(runtime.prefix or [])
        exec_cmd.append(cmd)

        audit_mode = ShellCommandExecutor.audit_mode_for_command(
            cmd,
            audit_files=audit_files,
        )
        audit_before = self._capture_shell_audit(audit_mode)
        started = time.perf_counter()

        try:
            session = await self._session_manager.start(ProcessSessionSpec(
                command=cmd,
                args=tuple([*(runtime.prefix or []), cmd]),
                cwd=str(workdir),
                display_cwd=self.relative_path(workdir),
                runtime=runtime_info,
                origin="tool",
                timeout_sec=timeout,
                idle_timeout_sec=idle_timeout,
                owner_cid=str(cid or ""),
                owner_sid=str(sid or ""),
                owner_run_id=str(run_id or ""),
                environment_id=str(environment_id or "").strip() or "default",
                audit_mode=audit_mode,
                audit_before=audit_before,
                env=env,
                sandbox_mode=sandbox_mode,
                additional_permissions=normalized_additional_permissions,
                tty=tty,
                terminal_size=terminal_size,
            ))
        except CapabilityError as exc:
            return self.fail_result(
                exc.code,
                tool="exec_command",
                command=cmd,
                cwd=self.relative_path(workdir),
                sandbox_mode=sandbox_mode,
                execution_backend=(
                    sandbox_backend_name()
                    if sandbox_mode in {
                        "read-only",
                        "workspace-read",
                        "workspace-write",
                    }
                    else ("native-pty" if tty else "local")
                ),
                detail=exc.message,
            )
        except SandboxError as exc:
            failure = map_sandbox_failure(exc)
            return self.fail_result(
                failure.reason,
                tool="exec_command",
                command=cmd,
                cwd=self.relative_path(workdir),
                sandbox_mode=sandbox_mode,
                execution_backend=sandbox_backend_name(),
                backend_code=failure.backend_code,
                stage=failure.stage,
                retryable=failure.retryable,
                detail=failure.detail,
            )
        except Exception as exc:
            execution_backend = (
                sandbox_backend_name()
                if sandbox_mode in {
                    "read-only",
                    "workspace-read",
                    "workspace-write",
                }
                else ("native-pty" if tty else "local")
            )
            observe_exception(
                "exec_command.internal_error",
                exc,
                tool="exec_command",
                execution_backend=execution_backend,
            )
            return self.fail_result(
                "tool_internal_error",
                tool="exec_command",
                command=cmd,
                cwd=self.relative_path(workdir),
                sandbox_mode=sandbox_mode,
                execution_backend=execution_backend,
                exception_type=type(exc).__name__,
            )

        process = session.process

        await wait_for_process(process, yield_ms)

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        data = await self._session_result(
            session,
            tool="exec_command",
            output_limit=output_limit,
            elapsed_ms=elapsed_ms,
            extra={
                "resolved_command": exec_cmd,
                "risk": policy.get("risk"),
                "category": policy.get("category"),
                "risk_signals": policy.get("reasons") or [],
                "execution_target": policy.get("execution_target"),
                "project_types": policy.get("project_types") or [],
                "timeout_sec": timeout,
                "idle_timeout_sec": idle_timeout,
                "yield_time_ms": yield_ms,
                "pty": tty,
                "pty_fallback": False,
                "sandbox_mode": sandbox_mode,
                "sandbox_permissions": permission,
                "execution_backend": (
                    sandbox_backend_name()
                    if sandbox_mode in {"read-only", "workspace-read", "workspace-write"}
                    else ("native-pty" if tty else "local")
                ),
            }
        )

        self._record_shell_result(data)
        return self._result_from_data("exec_command", data)

    async def write_stdin(
        self,
        *,
        session_id: str,
        stdin: str = "",
        wait_ms: int = WRITE_STDIN_DEFAULT_WAIT_MS,
        max_output_chars: int = 12000,
        control: str = "none",
        terminal_rows: int | None = None,
        terminal_columns: int | None = None,
        cid: str = "",
        sid: str = "",
        call_id: str = "",
    ) -> dict[str, typing.Any]:
        """向已有 shell 会话写入输入，或轮询增量输出。"""
        await self._session_manager.cleanup()

        exec_session_id = str(session_id or "").strip()
        if not exec_session_id:
            return self.fail_result("session_id_empty", tool="write_stdin")

        session = self._sessions.get(exec_session_id)
        if session is None:
            return self.fail_result(
                "exec_session_not_found",
                tool="write_stdin",
                session_id=exec_session_id
            )

        owner_error = self._session_owner_error(session, cid=cid, sid=sid)
        if owner_error is not None:
            return owner_error

        input_text: str = str(stdin or "")

        output_limit = self._bounded_int(
            max_output_chars,
            default=12000,
            minimum=1024,
            maximum=120000
        )

        control_name = str(control or "none").strip().lower() or "none"

        if control_name not in {
            "none",
            "interrupt",
            "eof",
            "resize",
            "terminate",
            "kill",
        }:
            return self.fail_result(
                "exec_control_invalid",
                tool="write_stdin",
                session_id=exec_session_id,
                control=control
            )
        wait_time = self._resolve_write_wait_time(
            wait_ms,
            input_text=input_text,
            control=control_name,
        )
        terminal_size: TerminalSize | None = None
        if control_name == "resize":
            if terminal_rows is None or terminal_columns is None:
                return self.fail_result(
                    "exec_terminal_size_required",
                    tool="write_stdin",
                    session_id=exec_session_id,
                )
            try:
                terminal_size = TerminalSize(
                    rows=terminal_rows,
                    columns=terminal_columns,
                )
            except (TypeError, ValueError) as exc:
                return self.fail_result(
                    "terminal_size_invalid",
                    tool="write_stdin",
                    session_id=exec_session_id,
                    detail=str(exc),
                )
        elif terminal_rows is not None or terminal_columns is not None:
            return self.fail_result(
                "exec_terminal_size_unexpected",
                tool="write_stdin",
                session_id=exec_session_id,
            )

        started = time.perf_counter()

        write_error = await self._apply_control_or_stdin(
            session,
            input_text=input_text,
            control=control_name,
            terminal_size=terminal_size,
        )
        if write_error is not None:
            return write_error

        await wait_for_process(session.process, wait_time)

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        data = await self._session_result(
            session,
            tool="write_stdin",
            output_limit=output_limit,
            elapsed_ms=elapsed_ms,
            extra={
                "control": control_name,
                "stdin_written": len(str(stdin or "")),
                "pty": session.tty,
                "pty_fallback": False,
                "terminal_rows": session.terminal_size.rows,
                "terminal_columns": session.terminal_size.columns,
            }
        )

        self._record_shell_result(data)
        return self._result_from_data("write_stdin", data)

    def _session_owner_error(
        self,
        session: ExecSession,
        *,
        cid: typing.Any,
        sid: typing.Any,
    ) -> dict[str, typing.Any] | None:
        """校验命令会话是否属于当前服务端会话。"""
        owner = (session.owner_cid, session.owner_sid)
        caller = (str(cid or ""), str(sid or ""))

        if owner == caller:
            return None

        return self.fail_result(
            "exec_session_owner_mismatch",
            tool="write_stdin",
            session_id=session.session_id,
            error="execution_policy_blocked",
        )

    async def running_sessions_snapshot(self) -> dict[str, typing.Any]:
        """返回当前仍在运行的命令会话摘要。"""
        return await self._session_manager.running_snapshot()

    async def session_output_snapshot(
        self,
        *,
        session_id: str,
        max_output_chars: int = 12000
    ) -> dict[str, typing.Any]:
        """返回命令会话的只读输出快照，不消费增量缓冲。"""
        sid = str(session_id or "").strip()
        output_limit = self._bounded_int(
            max_output_chars,
            default=12000,
            minimum=1024,
            maximum=120000
        )

        return await self._session_manager.output_snapshot(
            sid,
            max_output_chars=output_limit,
        )

    async def _apply_control_or_stdin(
        self,
        session: ExecSession,
        *,
        input_text: str,
        control: str,
        terminal_size: TerminalSize | None,
    ) -> dict[str, typing.Any] | None:
        """应用控制动作或向会话写入标准输入。"""
        try:
            failure = await self._session_manager.apply(
                session,
                input_text=input_text,
                control=control,
                terminal_size=terminal_size,
            )
        except SandboxError as error:
            mapped = map_sandbox_failure(error, control=control)
            return self.fail_result(
                mapped.reason,
                tool="write_stdin",
                session_id=session.session_id,
                exit_code=session.process.returncode,
                sandbox_mode=session.runtime.get("sandbox_mode"),
                execution_backend=sandbox_backend_name(),
                backend_code=mapped.backend_code,
                stage=mapped.stage,
                retryable=mapped.retryable,
                detail=mapped.detail,
            )

        if failure is None:
            return None

        return self.fail_result(
            failure,
            tool="write_stdin",
            session_id=session.session_id,
            exit_code=session.process.returncode,
        )

    async def _session_result(
        self,
        session: ExecSession,
        *,
        tool: str,
        output_limit: int,
        elapsed_ms: int,
        extra: dict[str, typing.Any] | None = None
    ) -> dict[str, typing.Any]:
        """生成会话当前状态和增量输出。"""
        await self._session_manager.finalize_if_exited(session)

        stdout, stderr, output_lines, dropped_stdout, dropped_stderr = await self._session_manager.drain(
            session,
            flush_pending=session.finalized
        )

        stdout_text = self.decode_bytes(stdout)
        stderr_text = self.decode_bytes(stderr)

        output_text = stdout_text
        if stderr_text:
            output_text = f"{output_text}{stderr_text}" if output_text else stderr_text

        clipped_output = self._session_manager.clip_output(output_text, output_limit)
        clipped_stdout = self._session_manager.clip_output(stdout_text, output_limit)
        clipped_stderr = self._session_manager.clip_output(stderr_text, output_limit)

        exit_code = session.process.returncode
        status = "running" if exit_code is None else "exited"
        timed_out = session.termination_reason == "expired"
        if time.monotonic() >= session.expires_monotonic and exit_code is None:
            session.termination_reason = "expired"
            timed_out = True

        if timed_out and exit_code is None:
            await self._session_manager.apply(session, control="kill")
            await wait_for_process(session.process, 1000)
            await self._session_manager.finalize_if_exited(session)

            exit_code = session.process.returncode
            status = "exited" if exit_code is not None else "running"

        execution_outcome_unknown = (
            isinstance(session.process, SidecarProcess)
            and session.process.execution_outcome_unknown
        )

        output_truncated = len(output_text) > output_limit
        stdout_truncated = len(stdout_text) > output_limit
        stderr_truncated = len(stderr_text) > output_limit

        data = {
            "tool": tool,
            "session_id": session.session_id,
            "command": session.command,
            "cwd": session.cwd,
            "status": status,
            "pid": session.process.pid,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "elapsed_ms": elapsed_ms,
            "runtime": dict(session.runtime),
            "runtime_name": session.runtime.get("name"),
            "sandbox_mode": session.runtime.get("sandbox_mode", "danger-full-access"),
            "pty": session.tty,
            "pty_fallback": False,
            "terminal_rows": session.terminal_size.rows,
            "terminal_columns": session.terminal_size.columns,
            "execution_backend": (
                sandbox_backend_name()
                if isinstance(session.process, SidecarProcess)
                else ("native-pty" if session.tty else "local")
            ),
            "output": clipped_output,
            "stdout": clipped_stdout,
            "stderr": clipped_stderr,
            "output_lines": list(output_lines),
            "output_truncated": output_truncated,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "truncated": output_truncated or stdout_truncated or stderr_truncated,
            "stdout_dropped": dropped_stdout,
            "stderr_dropped": dropped_stderr,
        }
        data.update(extra or {})
        if execution_outcome_unknown:
            data["execution_outcome_unknown"] = True

        if session.finalized:
            data["shell_file_changes"] = self._final_file_changes(session)
            data["shell_write_detected"] = bool(data["shell_file_changes"].get("changed"))
            self._session_manager.remove(session.session_id)

        controlled_stop = (
            tool == "write_stdin"
            and str(data.get("control") or "") in {"interrupt", "terminate", "kill"}
        )

        if execution_outcome_unknown or (
            not controlled_stop and (exit_code not in (None, 0) or timed_out)
        ):
            if execution_outcome_unknown:
                data["reason"] = "execution_outcome_unknown"
            else:
                data["reason"] = (
                    "command_timed_out" if timed_out else "command_failed"
                )
            self.core.enrich_failure_facts(data)

        return data

    def _capture_shell_audit(self, mode: str) -> dict[str, typing.Any] | None:
        """按审计模式采集文件指纹。"""
        if mode == "off":
            return None
        return self._file_audit.capture_file_fingerprints(hash_files=mode == "full")

    def _final_file_changes(self, session: ExecSession) -> dict[str, typing.Any]:
        """计算会话生命周期内的文件变化。"""
        if session.audit_mode == "off":
            return {
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

        audit_after = self._capture_shell_audit(session.audit_mode)
        return self._file_audit.diff_file_fingerprints(session.audit_before, audit_after)

    def _record_shell_result(self, data: dict[str, typing.Any]) -> None:
        """记录最近一次 shell 结果，供后续质量检查使用。"""
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

    def _policy_blocked_result(
        self,
        tool: str,
        command: str,
        cwd: str,
        policy: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """构造执行策略拒绝结果。"""
        data = {
            "tool": tool,
            "command": command,
            "cwd": cwd,
            "risk": policy.get("risk"),
            "category": policy.get("category"),
            "risk_signals": policy.get("reasons") or [],
            "execution_target": policy.get("execution_target"),
            "error": "execution_policy_blocked"
        }
        self._record_shell_result(data)
        return {
            "ok": False,
            "text": f"{tool} blocked by execution policy",
            "attachments": [],
            "data": data,
            "logs": []
        }

    @staticmethod
    def _result_from_data(
        tool: str,
        data: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """根据会话状态构造工具结果。"""
        status = str(data.get("status") or "running")
        exit_code = data.get("exit_code")
        ok = exit_code in (None, 0) and not bool(data.get("timed_out"))

        if (
            tool == "write_stdin"
            and str(data.get("control") or "") in {"interrupt", "terminate", "kill"}
            and status == "exited"
        ):
            ok = True

        if not ok:
            WorkspaceContext.enrich_failure_facts(data)

        return {
            "ok": ok,
            "text": f"{tool} {status} session_id={data.get('session_id')} elapsed_ms={data.get('elapsed_ms')}",
            "attachments": [],
            "data": data,
            "logs": []
        }

    @staticmethod
    def _bounded_int(
        value: typing.Any,
        *,
        default: int,
        minimum: int,
        maximum: int
    ) -> int:
        """把数值限制到指定范围。"""
        try:
            number = int(value if value is not None else default)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    @classmethod
    def _resolve_exec_yield_time(
        cls,
        value: typing.Any,
        *,
        windows: bool | None = None,
    ) -> int:
        """按平台解析首次命令调用的有效等待时间。"""
        is_windows = os.name == "nt" if windows is None else windows
        minimum = (
            EXEC_COMMAND_WINDOWS_MIN_YIELD_MS
            if is_windows
            else EXEC_COMMAND_MIN_YIELD_MS
        )
        return cls._bounded_int(
            value,
            default=EXEC_COMMAND_DEFAULT_YIELD_MS,
            minimum=minimum,
            maximum=EXEC_COMMAND_MAX_YIELD_MS,
        )

    @classmethod
    def _resolve_write_wait_time(
        cls,
        value: typing.Any,
        *,
        input_text: str,
        control: str,
    ) -> int:
        """区分纯轮询与交互操作的有效等待时间。"""
        empty_poll = not input_text and control == "none"
        return cls._bounded_int(
            value,
            default=WRITE_STDIN_DEFAULT_WAIT_MS,
            minimum=(
                WRITE_STDIN_EMPTY_MIN_WAIT_MS
                if empty_poll
                else WRITE_STDIN_MIN_WAIT_MS
            ),
            maximum=(
                WRITE_STDIN_EMPTY_MAX_WAIT_MS
                if empty_poll
                else WRITE_STDIN_MAX_WAIT_MS
            ),
        )


if __name__ == '__main__':
    pass
