# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
import secrets
from dataclasses import dataclass
from engine.observability import (
    observe,
    observe_exception
)
from mind_app.runtime.processes import (
    close_process_stdin,
    interrupt_process_tree,
    subprocess_process_group_kwargs,
    terminate_process_tree,
    wait_for_process
)
from mind_app.native_coding.encoding import decode_process_output
from mind_app.native_coding.exec.process_capture import (
    OrderedOutputBuffer,
    ProcessCapture
)


@dataclass(frozen=True, slots=True)
class ProcessSessionSpec(object):
    """描述一个本地进程会话的启动参数。"""

    command: str
    args: tuple[str, ...]
    cwd: str
    display_cwd: str
    runtime: dict[str, typing.Any]
    origin: str
    timeout_sec: int
    idle_timeout_sec: int
    owner_cid: str = ""
    owner_sid: str = ""
    audit_mode: str = "off"
    audit_before: dict[str, typing.Any] | None = None
    stdin_enabled: bool = True
    env: dict[str, str] | None = None


class ProcessSession(object):
    """保存一个可持续读取和控制的本地进程会话。"""

    def __init__(
        self,
        *,
        session_id: str,
        spec: ProcessSessionSpec,
        process: asyncio.subprocess.Process,
    ) -> None:
        """初始化进程会话及有限输出缓冲区。"""
        self.session_id       = session_id
        self.command          = spec.command
        self.args             = spec.args
        self.cwd              = spec.display_cwd
        self.process          = process
        self.runtime          = dict(spec.runtime)
        self.origin           = spec.origin
        self.owner_cid        = spec.owner_cid
        self.owner_sid        = spec.owner_sid
        self.started_at       = time.time()
        self.expires_at       = self.started_at + spec.timeout_sec
        self.idle_timeout_sec = spec.idle_timeout_sec
        self.audit_mode       = spec.audit_mode
        self.audit_before     = spec.audit_before

        self.stdout = bytearray()
        self.stderr = bytearray()

        self.output_buffer = OrderedOutputBuffer()

        self.display_output_buffer = OrderedOutputBuffer(
            max_lines=1000,
            max_line_chars=1000,
        )

        self.stdout_dropped = 0
        self.stderr_dropped = 0

        self.last_activity = self.started_at

        self.finalized = False

        self.stdout_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None

        self.lock = asyncio.Lock()


class ProcessSessionManager(object):
    """统一管理本地进程会话的生命周期和输出。"""

    BUFFER_LIMIT_BYTES   = 1_000_000
    IO_DRAIN_TIMEOUT_SEC = 2.0

    def __init__(self) -> None:
        """初始化进程会话表。"""
        self.sessions: dict[str, ProcessSession] = {}

    async def start(self, spec: ProcessSessionSpec) -> ProcessSession:
        """启动进程并注册可持续读取的会话。"""
        await self.cleanup()

        stdin = asyncio.subprocess.PIPE if spec.stdin_enabled else asyncio.subprocess.DEVNULL

        process = await asyncio.create_subprocess_exec(
            *spec.args,
            cwd=spec.cwd,
            env=spec.env,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **subprocess_process_group_kwargs(),
        )

        session = ProcessSession(
            session_id=f"exec_{secrets.token_hex(8)}",
            spec=spec,
            process=process,
        )

        session.stdout_task = asyncio.create_task(
            self._read_stream(session, "stdout")
        )

        session.stderr_task = asyncio.create_task(
            self._read_stream(session, "stderr")
        )
        self.sessions[session.session_id] = session
        observe(
            "process.started",
            session_id=session.session_id,
            pid=process.pid,
            runtime=session.runtime.get("name"),
            origin=session.origin,
            cid=session.owner_cid,
            sid=session.owner_sid,
        )

        return session

    def get(self, session_id: str) -> ProcessSession | None:
        """返回指定进程会话。"""
        return self.sessions.get(str(session_id or "").strip())

    def remove(self, session_id: str) -> None:
        """从会话表移除指定记录。"""
        self.sessions.pop(str(session_id or "").strip(), None)

    async def running_snapshot(self) -> dict[str, typing.Any]:
        """返回当前仍在运行的会话摘要。"""
        await self.cleanup()

        items = [
            {
                "session_id": session.session_id,
                "command": session.command,
                "cwd": session.cwd,
                "pid": session.process.pid,
                "started_at": session.started_at,
                "last_activity": session.last_activity,
                "origin": session.origin,
                "owner_cid": session.owner_cid,
                "owner_sid": session.owner_sid
            }
            for session in self.sessions.values()
            if session.process.returncode is None
        ]

        items.sort(key=lambda item: float(item.get("started_at") or 0.0))

        return {"count": len(items), "items": items}

    async def output_snapshot(
        self,
        session_id: str,
        *,
        max_output_chars: int,
    ) -> dict[str, typing.Any]:
        """返回不消费缓冲区的会话输出快照。"""
        await self.cleanup()

        sid = str(session_id or "").strip()
        if not sid:
            return {"ok": False, "reason": "session_id_empty", "session_id": sid}

        session = self.sessions.get(sid)
        if session is None:
            return {"ok": False, "reason": "exec_session_not_found", "session_id": sid}

        await self.finalize_if_exited(session)

        async with session.lock:
            stdout = bytes(session.stdout)
            stderr = bytes(session.stderr)
            stdout_dropped = session.stdout_dropped
            stderr_dropped = session.stderr_dropped

        output_lines = await session.display_output_buffer.snapshot()

        stdout_text = decode_process_output(stdout)
        stderr_text = decode_process_output(stderr)
        output_text = stdout_text

        if stderr_text:
            output_text = f"{output_text}{stderr_text}" if output_text else stderr_text
        if not output_text and output_lines:
            output_text = "\n".join(output_lines)

        limit = max(1024, min(120000, int(max_output_chars or 12000)))

        return {
            "ok"               : True,
            "tool"             : "exec_session_snapshot",
            "session_id"       : session.session_id,
            "command"          : session.command,
            "cwd"              : session.cwd,
            "status"           : "running" if session.process.returncode is None else "exited",
            "pid"              : session.process.pid,
            "exit_code"        : session.process.returncode,
            "started_at"       : session.started_at,
            "last_activity"    : session.last_activity,
            "runtime"          : dict(session.runtime),
            "runtime_name"     : session.runtime.get("name"),
            "origin"           : session.origin,
            "owner_cid"        : session.owner_cid,
            "owner_sid"        : session.owner_sid,
            "output"           : self._clip(output_text, limit),
            "stdout"           : self._clip(stdout_text, limit),
            "stderr"           : self._clip(stderr_text, limit),
            "output_lines"     : list(output_lines),
            "output_truncated" : len(output_text) > limit,
            "stdout_truncated" : len(stdout_text) > limit,
            "stderr_truncated" : len(stderr_text) > limit,
            "truncated"        : max(len(output_text), len(stdout_text), len(stderr_text)) > limit,
            "stdout_dropped"   : stdout_dropped,
            "stderr_dropped"   : stderr_dropped
        }

    @staticmethod
    async def apply(
        session: ProcessSession,
        *,
        input_text: str = "",
        control: str = "none",
    ) -> str | None:
        """向会话发送控制动作或标准输入。"""
        session.last_activity = time.time()

        process = session.process

        if control != "none":
            observe(
                "process.control",
                session_id=session.session_id,
                pid=process.pid,
                control=control,
            )
        elif input_text:
            observe(
                "process.input",
                session_id=session.session_id,
                pid=process.pid,
                bytes=len(input_text.encode(errors="replace")),
            )

        if control == "terminate":
            await terminate_process_tree(process, force=False)
            return None
        if control == "kill":
            await terminate_process_tree(process, force=True)
            return None
        if control == "interrupt":
            if not await interrupt_process_tree(process):
                return "exec_interrupt_failed"
            return None
        if control == "eof":
            await close_process_stdin(process)
            return None
        if not input_text:
            return None
        if process.returncode is not None:
            return "exec_session_exited"
        stdin_pipe = process.stdin
        if stdin_pipe is None or stdin_pipe.is_closing():
            return "exec_stdin_closed"

        stdin_pipe.write(input_text.encode())
        await stdin_pipe.drain()

        return None

    @staticmethod
    async def drain(
        session: ProcessSession,
        *,
        flush_pending: bool = False,
    ) -> tuple[bytes, bytes, tuple[str, ...], int, int]:
        """取出并清空会话自上次读取后的输出。"""
        async with session.lock:
            stdout = bytes(session.stdout)
            stderr = bytes(session.stderr)

            session.stdout.clear()
            session.stderr.clear()

            stdout_dropped = session.stdout_dropped
            stderr_dropped = session.stderr_dropped

            session.stdout_dropped = 0
            session.stderr_dropped = 0

            session.last_activity = time.time()

        output_lines = await session.output_buffer.drain(flush_pending=flush_pending)

        return stdout, stderr, output_lines, stdout_dropped, stderr_dropped

    async def cleanup(self) -> None:
        """终止过期会话并回收空闲的已退出记录。"""
        now = time.time()
        for session in list(self.sessions.values()):
            expired = now >= session.expires_at

            idle = now - session.last_activity >= session.idle_timeout_sec

            if session.process.returncode is None and (expired or idle):
                observe(
                    "process.cleanup",
                    session_id=session.session_id,
                    pid=session.process.pid,
                    reason="expired" if expired else "idle",
                )
                await terminate_process_tree(
                    session.process,
                    force=expired,
                )
                await wait_for_process(session.process, 1000)

            await self.finalize_if_exited(session)

            if session.finalized and (expired or idle):
                self.sessions.pop(session.session_id, None)

    async def stop_running_sessions(self) -> dict[str, typing.Any]:
        """终止并回收当前仍在运行的全部进程会话。"""
        await self.cleanup()

        sessions = [
            session
            for session in self.sessions.values()
            if session.process.returncode is None
        ]
        stopped: list[dict[str, typing.Any]] = []
        failures: list[dict[str, typing.Any]] = []

        for session in sessions:
            item = {
                "session_id" : session.session_id,
                "command"    : session.command,
                "pid"        : session.process.pid,
                "origin"     : session.origin,
            }
            try:
                await terminate_process_tree(
                    session.process,
                    force=False,
                )
                if session.process.returncode is None:
                    failures.append({**item, "reason": "process_still_running"})
                    continue
                await self.finalize_if_exited(session)
            except (OSError, RuntimeError, ValueError) as exc:
                observe_exception(
                    "process.stop.failed",
                    exc,
                    session_id=session.session_id,
                    pid=session.process.pid,
                )
                failures.append({
                    **item,
                    "reason": str(exc).strip() or type(exc).__name__,
                })
                continue

            self.sessions.pop(session.session_id, None)
            stopped.append({
                **item,
                "exit_code": session.process.returncode,
            })

        result = {
            "ok"        : not failures,
            "requested" : len(sessions),
            "stopped"   : len(stopped),
            "failed"    : len(failures),
            "items"     : stopped,
            "failures"  : failures,
        }
        observe(
            "process.stop_all.complete",
            requested=result["requested"],
            stopped=result["stopped"],
            failed=result["failed"],
        )
        return result

    async def close(self) -> None:
        """终止并回收全部进程会话。"""
        await self.stop_running_sessions()
        sessions = list(self.sessions.values())

        for session in sessions:
            if session.process.returncode is None:
                await terminate_process_tree(session.process, force=True)
            await self.finalize_if_exited(session)

        self.sessions.clear()

    async def finalize_if_exited(self, session: ProcessSession) -> None:
        """在进程退出后收束输出读取任务。"""
        if session.finalized or session.process.returncode is None:
            return None

        await close_process_stdin(session.process)

        tasks = [
            task
            for task in (session.stdout_task, session.stderr_task)
            if task is not None
        ]

        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self.IO_DRAIN_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        ProcessCapture.close_process_transport(session.process)

        session.finalized = True

        observe(
            "process.exited",
            session_id=session.session_id,
            pid=session.process.pid,
            exit_code=session.process.returncode,
            elapsed_ms=int(max(0.0, time.time() - session.started_at) * 1000),
            stdout_dropped=session.stdout_dropped,
            stderr_dropped=session.stderr_dropped,
        )

    async def _read_stream(self, session: ProcessSession, name: str) -> None:
        """持续读取会话输出流。"""
        stream = session.process.stdout if name == "stdout" else session.process.stderr

        if stream is None:
            return None

        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return None
            async with session.lock:
                target = session.stdout if name == "stdout" else session.stderr
                target.extend(chunk)
                if len(target) > self.BUFFER_LIMIT_BYTES:
                    overflow = len(target) - self.BUFFER_LIMIT_BYTES
                    del target[:overflow]
                    if name == "stdout":
                        session.stdout_dropped += overflow
                    else:
                        session.stderr_dropped += overflow

                await session.output_buffer.append(name, chunk)
                await session.display_output_buffer.append(name, chunk)

                session.last_activity = time.time()

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        """按字符上限截断输出文本。"""
        if len(value) <= limit:
            return value
        return f"{value[:limit]}\n...[truncated {len(value) - limit} chars]"


if __name__ == '__main__':
    pass
