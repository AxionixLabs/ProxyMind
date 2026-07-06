# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import time
import signal
import typing
import asyncio
import secrets
from mind_app.native_coding.base import (
    NativeCodingBase, NativeCodingComponent
)
from mind_app.native_coding.exec.shell_exec import ShellCommandTools
from mind_app.native_coding.exec.shell_runtime import ShellRuntimeResolver


class ExecSession:
    """记录一个可持续读写的 shell 进程会话。"""

    __slots__ = (
        "session_id",
        "command",
        "cwd",
        "process",
        "runtime",
        "started_at",
        "expires_at",
        "idle_timeout_sec",
        "audit_mode",
        "audit_before",
        "stdout",
        "stderr",
        "stdout_dropped",
        "stderr_dropped",
        "last_activity",
        "finalized",
        "stdout_task",
        "stderr_task",
        "lock",
    )

    def __init__(
        self,
        *,
        session_id: str,
        command: str,
        cwd: str,
        process: asyncio.subprocess.Process,
        runtime: dict[str, typing.Any],
        started_at: float,
        expires_at: float,
        idle_timeout_sec: int,
        audit_mode: str,
        audit_before: dict[str, typing.Any] | None
    ) -> None:
        """初始化 shell 进程会话和增量输出缓冲区。"""
        self.session_id = session_id
        self.command    = command
        self.cwd        = cwd
        self.process    = process
        self.runtime    = runtime
        self.started_at = started_at
        self.expires_at = expires_at

        self.idle_timeout_sec = idle_timeout_sec

        self.audit_mode   = audit_mode
        self.audit_before = audit_before

        self.stdout = bytearray()
        self.stderr = bytearray()

        self.stdout_dropped = 0
        self.stderr_dropped = 0

        self.last_activity = time.time()

        self.finalized = False

        self.stdout_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None

        self.lock = asyncio.Lock()


class ExecCommandTools(NativeCodingComponent):
    """提供可持续读写的 shell 会话工具。"""

    BUFFER_LIMIT_BYTES = 1_000_000

    def __init__(
        self,
        core: NativeCodingBase,
        *,
        command_policy: typing.Any,
        file_audit: typing.Any
    ) -> None:
        """保存共享运行时、执行策略和会话表。"""
        super().__init__(core)

        self._command_policy = command_policy
        self._file_audit     = file_audit

        self._sessions: dict[str, ExecSession] = {}

    async def exec_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        yield_time_ms: int = 1000,
        max_output_chars: int = 24000,
        timeout_sec: int = 1800,
        idle_timeout_sec: int = 300,
        execution: dict[str, typing.Any] | None = None,
        audit_files: bool = True
    ) -> dict[str, typing.Any]:
        """启动一个可持续读取和写入的 shell 命令会话。"""
        await self._cleanup_sessions()

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
            return self._policy_blocked_result("exec_command", cmd, cwd, policy)

        workdir = self.resolve_path(cwd)
        if not workdir.is_dir():
            return self.fail_result(
                "cwd_not_directory",
                tool="exec_command",
                command=cmd,
                cwd=cwd
            )

        if policy.get("execution_target") == "cloud_sandbox":
            return self.ok_result(
                "exec_command requires cloud sandbox",
                ok=False,
                tool="exec_command",
                command=cmd,
                cwd=self.relative_path(workdir),
                execution_target="cloud_sandbox",
                requires_cloud_sandbox=True,
                execution=policy.get("execution"),
                grant_id=policy.get("grant_id")
            )

        timeout = self._bounded_int(
            policy.get("timeout_sec"), default=timeout_sec, minimum=1, maximum=7200
        )
        idle_timeout = self._bounded_int(
            idle_timeout_sec, default=300, minimum=1, maximum=1800
        )
        yield_ms = self._bounded_int(
            yield_time_ms, default=1000, minimum=0, maximum=30000
        )
        output_limit = self._bounded_int(
            max_output_chars, default=self.max_output_chars, minimum=1024, maximum=120000
        )

        env     = os.environ.copy()
        runtime = ShellRuntimeResolver.resolve(env=env)

        runtime_info = {
            "name"       : runtime.name,
            "syntax"     : runtime.syntax,
            "executable" : runtime.executable,
            "source"     : runtime.source
        }

        exec_cmd = list(runtime.prefix or [])
        exec_cmd.append(cmd)

        audit_mode   = ShellCommandTools.audit_mode_for_command(cmd, audit_files=audit_files)
        audit_before = self._capture_shell_audit(audit_mode)
        started      = time.perf_counter()

        process = await asyncio.create_subprocess_exec(
            *(runtime.prefix or []),
            cmd,
            cwd=str(workdir),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        session = ExecSession(
            session_id=self._new_session_id(),
            command=cmd,
            cwd=self.relative_path(workdir),
            process=process,
            runtime=runtime_info,
            started_at=time.time(),
            expires_at=time.time() + timeout,
            idle_timeout_sec=idle_timeout,
            audit_mode=audit_mode,
            audit_before=audit_before
        )
        session.stdout_task = asyncio.create_task(self._read_stream(session, "stdout"))
        session.stderr_task = asyncio.create_task(self._read_stream(session, "stderr"))
        self._sessions[session.session_id] = session

        await self._wait_for_process(process, yield_ms)

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        data = await self._session_result(
            session,
            tool="exec_command",
            output_limit=output_limit,
            elapsed_ms=elapsed_ms,
            extra={
                "resolved_command"       : exec_cmd,
                "risk"                   : policy.get("risk"),
                "category"               : policy.get("category"),
                "risk_signals"           : policy.get("reasons") or [],
                "approval_required"      : bool(policy.get("approval_required")),
                "execution_target"       : policy.get("execution_target"),
                "requires_cloud_sandbox" : bool(policy.get("requires_cloud_sandbox")),
                "execution"              : policy.get("execution"),
                "grant_id"               : policy.get("grant_id"),
                "project_types"          : policy.get("project_types") or [],
                "timeout_sec"            : timeout,
                "idle_timeout_sec"       : idle_timeout,
                "yield_time_ms"          : yield_ms,
                "pty"                    : False,
                "pty_fallback"           : True
            }
        )

        self._record_shell_result(data)
        return self._result_from_data("exec_command", data)

    async def write_stdin(
        self,
        *,
        session_id: str,
        stdin: str = "",
        wait_ms: int = 1000,
        max_output_chars: int = 12000,
        control: str = "none"
    ) -> dict[str, typing.Any]:
        """向已有 shell 会话写入输入，或轮询增量输出。"""
        await self._cleanup_sessions()

        sid = str(session_id or "").strip()
        if not sid:
            return self.fail_result("session_id_empty", tool="write_stdin")

        session = self._sessions.get(sid)
        if session is None:
            return self.fail_result(
                "exec_session_not_found",
                tool="write_stdin",
                session_id=sid
            )

        output_limit = self._bounded_int(
            max_output_chars,
            default=12000,
            minimum=1024,
            maximum=120000
        )

        wait_time    = self._bounded_int(wait_ms, default=1000, minimum=0, maximum=30000)
        control_name = str(control or "none").strip().lower() or "none"

        if control_name not in {"none", "interrupt", "eof", "terminate", "kill"}:
            return self.fail_result(
                "exec_control_invalid",
                tool="write_stdin",
                session_id=sid,
                control=control
            )

        started = time.perf_counter()

        write_error = await self._apply_control_or_stdin(
            session,
            input_text=str(stdin or ""),
            control=control_name
        )
        if write_error is not None:
            return write_error

        await self._wait_for_process(session.process, wait_time)

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        data = await self._session_result(
            session,
            tool="write_stdin",
            output_limit=output_limit,
            elapsed_ms=elapsed_ms,
            extra={
                "control"       : control_name,
                "stdin_written" : len(str(stdin or "")),
                "pty"           : False,
                "pty_fallback"  : True
            }
        )

        self._record_shell_result(data)
        return self._result_from_data("write_stdin", data)

    async def _apply_control_or_stdin(
        self,
        session: ExecSession,
        *,
        input_text: str,
        control: str
    ) -> dict[str, typing.Any] | None:
        """应用控制动作或向会话写入标准输入。"""
        process = session.process

        session.last_activity = time.time()

        if control == "terminate":
            process.terminate()
            return None
        if control == "kill":
            process.kill()
            return None
        if control == "interrupt":
            try:
                process.send_signal(signal.SIGINT)
            except (ProcessLookupError, RuntimeError, ValueError):
                return self.fail_result(
                    "exec_interrupt_failed",
                    tool="write_stdin",
                    session_id=session.session_id
            )
            return None
        if control == "eof":
            stdin_pipe = process.stdin
            if stdin_pipe is not None and not stdin_pipe.is_closing():
                stdin_pipe.close()
            return None

        if not input_text:
            return None
        if process.returncode is not None:
            return self.fail_result(
                "exec_session_exited",
                tool="write_stdin",
                session_id=session.session_id,
                exit_code=process.returncode
            )
        stdin_pipe = process.stdin
        if stdin_pipe is None or stdin_pipe.is_closing():
            return self.fail_result(
                "exec_stdin_closed",
                tool="write_stdin",
                session_id=session.session_id
            )

        stdin_pipe.write(input_text.encode())
        await stdin_pipe.drain()
        return None

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
        await self._finalize_if_exited(session)

        stdout, stderr, dropped_stdout, dropped_stderr = await self._drain_output(session)

        stdout_text = self.decode_bytes(stdout)
        stderr_text = self.decode_bytes(stderr)

        output_text = stdout_text
        if stderr_text:
            output_text = f"{output_text}{stderr_text}" if output_text else stderr_text

        clipped_output = self.clip_output(output_text, max_chars=output_limit)
        clipped_stdout = self.clip_output(stdout_text, max_chars=output_limit)
        clipped_stderr = self.clip_output(stderr_text, max_chars=output_limit)

        exit_code = session.process.returncode
        status    = "running" if exit_code is None else "exited"
        timed_out = time.time() >= session.expires_at and exit_code is None

        if timed_out:
            session.process.kill()
            await self._wait_for_process(session.process, 1000)
            await self._finalize_if_exited(session)
            exit_code = session.process.returncode
            status    = "exited" if exit_code is not None else "running"

        output_truncated = len(output_text) > output_limit
        stdout_truncated = len(stdout_text) > output_limit
        stderr_truncated = len(stderr_text) > output_limit

        data = {
            "tool"             : tool,
            "session_id"       : session.session_id,
            "command"          : session.command,
            "cwd"              : session.cwd,
            "status"           : status,
            "pid"              : session.process.pid,
            "exit_code"        : exit_code,
            "timed_out"        : timed_out,
            "elapsed_ms"       : elapsed_ms,
            "runtime"          : dict(session.runtime),
            "runtime_name"     : session.runtime.get("name"),
            "output"           : clipped_output,
            "stdout"           : clipped_stdout,
            "stderr"           : clipped_stderr,
            "output_truncated" : output_truncated,
            "stdout_truncated" : stdout_truncated,
            "stderr_truncated" : stderr_truncated,
            "truncated"        : output_truncated or stdout_truncated or stderr_truncated,
            "stdout_dropped"   : dropped_stdout,
            "stderr_dropped"   : dropped_stderr,
        }
        data.update(extra or {})

        if session.finalized:
            data["shell_file_changes"] = self._final_file_changes(session)
            data["shell_write_detected"] = bool(data["shell_file_changes"].get("changed"))
            self._sessions.pop(session.session_id, None)

        controlled_stop = (
            tool == "write_stdin"
            and str(data.get("control") or "") in {"interrupt", "terminate", "kill"}
        )

        if not controlled_stop and (exit_code not in (None, 0) or timed_out):
            data["reason"] = "command_timed_out" if timed_out else "command_failed"
            self.core.enrich_failure_facts(data)

        return data

    async def _read_stream(self, session: ExecSession, name: str) -> None:
        """持续读取进程输出流并写入会话缓冲区。"""
        stream = session.process.stdout if name == "stdout" else session.process.stderr
        if stream is None:
            return None

        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return None
            await self._append_output(session, name, chunk)

    async def _append_output(
        self,
        session: ExecSession,
        name: str,
        chunk: bytes
    ) -> None:
        """把输出追加到指定缓冲区，并限制缓冲区大小。"""
        async with session.lock:
            target = session.stdout if name == "stdout" else session.stderr
            target.extend(chunk)
            dropped_key = "stdout_dropped" if name == "stdout" else "stderr_dropped"
            if len(target) > self.BUFFER_LIMIT_BYTES:
                overflow = len(target) - self.BUFFER_LIMIT_BYTES
                del target[:overflow]
                if dropped_key == "stdout_dropped":
                    session.stdout_dropped += overflow
                else:
                    session.stderr_dropped += overflow
            session.last_activity = time.time()

    @staticmethod
    async def _drain_output(
        session: ExecSession
    ) -> tuple[bytes, bytes, int, int]:
        """取出并清空会话自上次读取后的输出。"""
        async with session.lock:
            stdout = bytes(session.stdout)
            stderr = bytes(session.stderr)
            session.stdout.clear()
            session.stderr.clear()
            dropped_stdout = session.stdout_dropped
            dropped_stderr = session.stderr_dropped
            session.stdout_dropped = 0
            session.stderr_dropped = 0
            session.last_activity = time.time()
        return stdout, stderr, dropped_stdout, dropped_stderr

    @staticmethod
    async def _wait_for_process(
        process: asyncio.subprocess.Process,
        timeout_ms: int
    ) -> None:
        """等待进程退出或达到等待时间。"""
        if timeout_ms <= 0 or process.returncode is not None:
            return None
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_ms / 1000)
        except asyncio.TimeoutError:
            return None

    @staticmethod
    async def _finalize_if_exited(session: ExecSession) -> None:
        """进程退出后收束读取任务。"""
        if session.finalized or session.process.returncode is None:
            return None

        tasks = [
            task for task in (session.stdout_task, session.stderr_task)
            if task is not None
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        session.finalized = True

    async def _cleanup_sessions(self) -> None:
        """清理过期、空闲或已经退出的会话。"""
        now = time.time()

        sessions = list(self._sessions.values())
        for session in sessions:
            expired = now >= session.expires_at
            idle    = now - session.last_activity >= session.idle_timeout_sec

            if session.process.returncode is None and (expired or idle):
                session.process.kill()
                await self._wait_for_process(session.process, 1000)

            await self._finalize_if_exited(session)

            if session.finalized and (expired or idle):
                self._sessions.pop(session.session_id, None)

    def _capture_shell_audit(self, mode: str) -> dict[str, typing.Any] | None:
        """按审计模式采集文件指纹。"""
        if mode == "off":
            return None
        return self._file_audit.capture_file_fingerprints(hash_files=mode == "full")

    def _final_file_changes(self, session: ExecSession) -> dict[str, typing.Any]:
        """计算会话生命周期内的文件变化。"""
        if session.audit_mode == "off":
            return {
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

        audit_after = self._capture_shell_audit(session.audit_mode)
        return self._file_audit.diff_file_fingerprints(session.audit_before, audit_after)

    def _policy_blocked_result(
        self,
        tool: str,
        command: str,
        cwd: str,
        policy: dict[str, typing.Any]
    ) -> dict[str, typing.Any]:
        """构造执行策略拒绝结果。"""
        data = {
            "tool"                   : tool,
            "command"                : command,
            "cwd"                    : cwd,
            "risk"                   : policy.get("risk"),
            "category"               : policy.get("category"),
            "risk_signals"           : policy.get("reasons") or [],
            "approval_required"      : bool(policy.get("approval_required")),
            "execution_target"       : policy.get("execution_target"),
            "requires_cloud_sandbox" : bool(policy.get("requires_cloud_sandbox")),
            "execution"              : policy.get("execution"),
            "grant_id"               : policy.get("grant_id"),
            "error"                  : "execution_policy_blocked"
        }
        self._record_shell_result(data)
        return {
            "ok"          : False,
            "text"        : f"{tool} blocked by execution policy",
            "attachments" : [],
            "data"        : data,
            "logs"        : []
        }

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

    @staticmethod
    def _result_from_data(tool: str, data: dict[str, typing.Any]) -> dict[str, typing.Any]:
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
            NativeCodingBase.enrich_failure_facts(data)
        return {
            "ok"          : ok,
            "text"        : f"{tool} {status} session_id={data.get('session_id')} elapsed_ms={data.get('elapsed_ms')}",
            "attachments" : [],
            "data"        : data,
            "logs"        : []
        }

    @staticmethod
    def _new_session_id() -> str:
        """生成不可预测的本地会话 ID。"""
        return f"exec_{secrets.token_hex(8)}"

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


if __name__ == '__main__':
    pass
