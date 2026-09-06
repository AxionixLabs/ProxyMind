# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import secrets
import time
import typing
from collections import deque
from dataclasses import dataclass

from agent.ports import (
    NetworkBlockedHandlerFactory,
    InteractiveProcessCapability,
    ProcessCapability,
    ProcessHandle,
    ProcessSpec,
)
from infrastructure.platform.encoding import decode_process_output
from infrastructure.platform.process_capture import (
    OrderedOutputBuffer,
    ProcessCapture
)
from infrastructure.platform.processes import (
    close_process_stdin,
    interrupt_process_tree,
    subprocess_process_group_kwargs,
    terminate_process_tree,
    wait_for_process
)
from infrastructure.platform.sandbox import (
    SandboxClient,
    SandboxUnavailable,
    SidecarProcess,
)
from infrastructure.platform.network import ManagedNetworkProxy
from observability import (
    observe,
    observe_exception
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
    owner_run_id: str = ""
    environment_id: str = ""
    execution_id: str = ""
    audit_mode: str = "off"
    audit_before: dict[str, typing.Any] | None = None
    stdin_enabled: bool = True
    env: dict[str, str] | None = None
    background: bool | None = None
    sandbox_mode: str = "danger-full-access"
    additional_permissions: dict[str, typing.Any] | None = None


class _CapabilityProcess:
    """把 ProcessCapability 句柄纳入既有进程会话生命周期。"""

    def __init__(self, handle: ProcessHandle) -> None:
        """绑定能力句柄并保留退出状态快照。"""
        self.handle = handle
        self.pid = handle.pid
        self.returncode: int | None = handle.returncode

    async def wait(self) -> int:
        """等待能力句柄退出并同步退出码。"""
        self.returncode = int(await self.handle.wait())
        return self.returncode

    async def terminate(self, *, force: bool = False) -> None:
        """通过能力端口终止进程。"""
        await self.handle.terminate(force=force)

    async def close(self) -> None:
        """关闭能力句柄并回收进程。"""
        await self.handle.aclose()


class ProcessSession(object):
    """保存一个可持续读取和控制的本地进程会话。"""

    def __init__(
        self,
        *,
        session_id: str,
        spec: ProcessSessionSpec,
        process: asyncio.subprocess.Process | SidecarProcess | _CapabilityProcess,
        network_proxy: ManagedNetworkProxy | None = None,
    ) -> None:
        """初始化进程会话及有限输出缓冲区。"""
        self.session_id = session_id
        self.command = spec.command
        self.args = spec.args
        self.cwd = spec.display_cwd
        self.process = process
        self.runtime = dict(spec.runtime)
        self.origin = spec.origin
        self.background = (
            bool(spec.background)
            if spec.background is not None
            else spec.origin != "tui_shell"
        )
        self.owner_cid = spec.owner_cid
        self.owner_sid = spec.owner_sid
        self.owner_run_id = spec.owner_run_id
        self.environment_id = spec.environment_id
        self.network_proxy = network_proxy
        self.started_at = time.time()
        self.expires_at = self.started_at + spec.timeout_sec
        self.idle_timeout_sec = spec.idle_timeout_sec
        self.audit_mode = spec.audit_mode
        self.audit_before = spec.audit_before

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
        self.output_revision = 0
        self.update_event = asyncio.Event()
        self.output_events: deque[tuple[int, str, bytes]] = deque(
            maxlen=256,
        )

        self.finalized = False

        self.stdout_task: asyncio.Task[None] | None = None
        self.stderr_task: asyncio.Task[None] | None = None
        self.exit_watch_task: asyncio.Task[None] | None = None

        self.lock = asyncio.Lock()


class ProcessSessionManager(object):
    """统一管理本地进程会话的生命周期和输出。"""

    BUFFER_LIMIT_BYTES = 1_000_000
    IO_DRAIN_TIMEOUT_SEC = 2.0

    def __init__(
        self,
        sandbox_client: SandboxClient | None = None,
        *,
        process_capability: ProcessCapability | None = None,
        interactive_process_capability: InteractiveProcessCapability | None = None,
        network_proxy: ManagedNetworkProxy | None = None,
        network_blocked_handler_factory: NetworkBlockedHandlerFactory | None = None,
    ) -> None:
        """初始化进程会话表。"""
        self.sessions: dict[str, ProcessSession] = {}
        self._change_revision: int = 0
        self._change_event = asyncio.Event()
        self._sandbox_client = sandbox_client
        self._process_capability = process_capability
        self._interactive_process_capability = interactive_process_capability
        self._network_proxy = network_proxy
        self._network_blocked_handler_factory = network_blocked_handler_factory

    @property
    def change_revision(self) -> int:
        """返回全局进程会话变更版本。"""
        return self._change_revision

    def _notify_change(self) -> None:
        """发布一次进程会话变更。"""
        self._change_revision += 1
        self._change_event.set()

    async def wait_for_change(
        self,
        *,
        revision: int,
        timeout_sec: float,
    ) -> dict[str, typing.Any]:
        """等待任意进程会话变更。"""
        requested = int(revision)
        if self._change_revision != requested:
            return {
                "changed": True,
                "revision": self._change_revision,
            }

        event = self._change_event
        event.clear()
        if self._change_revision != requested:
            return {
                "changed": True,
                "revision": self._change_revision,
            }

        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=max(0.01, float(timeout_sec)),
            )
        except asyncio.TimeoutError:
            return {
                "changed": False,
                "revision": self._change_revision,
            }

        return {
            "changed": True,
            "revision": self._change_revision,
        }

    async def start(self, spec: ProcessSessionSpec) -> ProcessSession:
        """启动进程并注册可持续读取的会话。"""
        if spec.sandbox_mode not in {
            "danger-full-access",
            "read-only",
            "workspace-read",
            "workspace-write",
        }:
            raise ValueError(f"sandbox_mode_invalid: {spec.sandbox_mode}")
        await self.cleanup()

        session_id = f"exec_{secrets.token_hex(8)}"
        execution_id = str(spec.execution_id or "").strip() or session_id
        process_env = dict(spec.env) if spec.env is not None else None
        session_network_proxy: ManagedNetworkProxy | None = None
        if (
            self._network_proxy is not None
            and spec.sandbox_mode in {"read-only", "workspace-read", "workspace-write"}
        ):
            callback_factory = self._network_blocked_handler_factory
            blocked_handler = (
                callback_factory(
                    str(spec.owner_sid or "").strip(),
                    str(spec.owner_run_id or "").strip(),
                    str(spec.environment_id or "").strip() or "default",
                    execution_id,
                )
                if callback_factory is not None
                else None
            )
            session_network_proxy = self._network_proxy.for_session(
                session_id=spec.owner_sid,
                on_blocked=blocked_handler,
            )
            await session_network_proxy.start()
            process_env = session_network_proxy.environment(process_env)

        try:
            if (
                self._process_capability is not None
                and spec.sandbox_mode == "danger-full-access"
            ):
                process_spec = ProcessSpec(
                    argv=spec.args,
                    cwd=spec.cwd,
                    env=process_env or {},
                    sandbox_mode=spec.sandbox_mode,
                    sandbox_permissions=(
                        "with_additional_permissions"
                        if spec.additional_permissions is not None
                        else "use_default"
                    ),
                    additional_permissions=spec.additional_permissions,
                    stdin_open=spec.stdin_enabled,
                )
                handle = await self._process_capability.spawn(process_spec)
                process = _CapabilityProcess(handle)
            elif spec.sandbox_mode in {"read-only", "workspace-read", "workspace-write"}:
                if self._sandbox_client is None:
                    raise SandboxUnavailable("sandbox client is not configured")
                spawn_kwargs: dict[str, typing.Any] = {
                    "argv": spec.args,
                    "cwd": spec.cwd,
                    "env": process_env or {},
                    "sandbox_mode": spec.sandbox_mode,
                    "stdin_open": spec.stdin_enabled,
                    "timeout_ms": max(1, int(spec.timeout_sec)) * 1000,
                }
                if spec.additional_permissions is not None:
                    spawn_kwargs["additional_permissions"] = spec.additional_permissions
                process = await self._sandbox_client.spawn(
                    **spawn_kwargs,
                )
            else:
                stdin = asyncio.subprocess.PIPE if spec.stdin_enabled else asyncio.subprocess.DEVNULL

                process = await asyncio.create_subprocess_exec(
                    *spec.args,
                    cwd=spec.cwd,
                    env=process_env,
                    stdin=stdin,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **subprocess_process_group_kwargs(),
                )
        except BaseException:
            if session_network_proxy is not None:
                await session_network_proxy.close()
            raise

        session = ProcessSession(
            session_id=session_id,
            spec=spec,
            process=process,
            network_proxy=session_network_proxy,
        )

        session.stdout_task = asyncio.create_task(
            self._read_stream(session, "stdout")
        )

        session.stderr_task = asyncio.create_task(
            self._read_stream(session, "stderr")
        )
        self.sessions[session.session_id] = session
        self._notify_change()
        session.exit_watch_task = asyncio.create_task(
            self._watch_process_exit(session),
            name=f"watch process exit {session.session_id}",
        )
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
                "background": session.background,
                "owner_cid": session.owner_cid,
                "owner_sid": session.owner_sid,
            }
            for session in self.sessions.values()
            if session.process.returncode is None
        ]

        items.sort(key=lambda item: float(item.get("started_at") or 0.0))

        return {
            "count": len(items),
            "items": items,
            "background_count": sum(
                1 for item in items if item.get("background")
            ),
            "background_items": [
                item for item in items if item.get("background")
            ],
            "user_shell_items": [
                item for item in items
                if item.get("origin") == "tui_shell" and not item.get("background")
            ],
        }

    async def mark_background(self, session_id: str) -> bool:
        """把指定会话标记为可由后台终端集合管理。"""
        session = self.get(session_id)
        if session is None:
            return False
        session.background = True
        session.output_revision += 1
        session.update_event.set()
        self._notify_change()
        return True

    async def wait_for_update(
        self,
        session_id: str,
        *,
        revision: int,
        timeout_sec: float,
    ) -> bool:
        """等待输出或退出状态变化，超时返回 False。"""
        session = self.get(session_id)
        if session is None:
            return False
        if (
            session.output_revision != int(revision)
            or session.process.returncode is not None
        ):
            return True

        event = session.update_event
        event.clear()
        if (
            session.output_revision != int(revision)
            or session.process.returncode is not None
        ):
            return True
        try:
            await asyncio.wait_for(
                event.wait(),
                timeout=max(0.01, float(timeout_sec)),
            )
        except asyncio.TimeoutError:
            return False
        return True

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
            "ok": True,
            "tool": "exec_session_snapshot",
            "session_id": session.session_id,
            "command": session.command,
            "cwd": session.cwd,
            "status": "running" if session.process.returncode is None else "exited",
            "pid": session.process.pid,
            "exit_code": session.process.returncode,
            "started_at": session.started_at,
            "last_activity": session.last_activity,
            "runtime": dict(session.runtime),
            "runtime_name": session.runtime.get("name"),
            "origin": session.origin,
            "background": session.background,
            "revision": session.output_revision,
            "owner_cid": session.owner_cid,
            "owner_sid": session.owner_sid,
            "output": self._clip(output_text, limit),
            "stdout": self._clip(stdout_text, limit),
            "stderr": self._clip(stderr_text, limit),
            "output_lines": list(output_lines),
            "output_lines_dropped": session.display_output_buffer.dropped_lines,
            "output_truncated": len(output_text) > limit,
            "stdout_truncated": len(stdout_text) > limit,
            "stderr_truncated": len(stderr_text) > limit,
            "truncated": max(len(output_text), len(stdout_text), len(stderr_text)) > limit,
            "stdout_dropped": stdout_dropped,
            "stderr_dropped": stderr_dropped
        }

    async def output_delta(
        self,
        session_id: str,
        *,
        revision: int,
    ) -> dict[str, typing.Any]:
        """返回指定版本之后可用的进程输出增量。"""
        session = self.get(session_id)
        if session is None:
            return {
                "revision": int(revision),
                "reset": True,
                "items": [],
                "snapshot": {
                    "ok": False,
                    "reason": "exec_session_not_found",
                    "session_id": str(session_id or "").strip(),
                },
            }

        requested = max(0, int(revision))
        async with session.lock:
            events = tuple(session.output_events)
            current_revision = session.output_revision
            status = (
                "running"
                if session.process.returncode is None
                else "exited"
            )
            exit_code = session.process.returncode

        reset = bool(
            current_revision > requested
            and (
                not events
                or requested < events[0][0] - 1
            )
        )
        items = [
            {
                "revision": event_revision,
                "stream": stream,
                "text": decode_process_output(chunk),
            }
            for event_revision, stream, chunk in events
            if event_revision > requested
        ]

        output_lines = await session.display_output_buffer.snapshot()
        return {
            "revision": current_revision,
            "reset": reset,
            "items": items,
            "snapshot": {
                "ok": True,
                "tool": "exec_session_update",
                "session_id": session.session_id,
                "command": session.command,
                "cwd": session.cwd,
                "status": status,
                "pid": session.process.pid,
                "exit_code": exit_code,
                "started_at": session.started_at,
                "last_activity": session.last_activity,
                "runtime": dict(session.runtime),
                "runtime_name": session.runtime.get("name"),
                "origin": session.origin,
                "background": session.background,
                "revision": current_revision,
                "owner_cid": session.owner_cid,
                "owner_sid": session.owner_sid,
                "output_lines": list(output_lines),
                "output_lines_dropped": session.display_output_buffer.dropped_lines,
            },
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

        if isinstance(process, SidecarProcess):
            if control in {"terminate", "kill"}:
                await process.client.terminate(process.process_id, signal="terminate")
                return None
            if control == "interrupt":
                await process.client.terminate(process.process_id, signal="interrupt")
                return None
            if control == "eof":
                await process.client.write(process.process_id, eof=True)
                return None
            if not input_text:
                return None
            if process.returncode is not None:
                return "exec_session_exited"
            await process.client.write(
                process.process_id,
                data=input_text.encode(),
            )
            return None

        if isinstance(process, _CapabilityProcess):
            if control in {"terminate", "kill"}:
                await process.terminate(force=control == "kill")
                return None
            if control == "interrupt":
                await process.terminate(force=False)
                return None
            if control == "eof":
                await process.handle.write("", eof=True)
                return None
            if not input_text:
                return None
            if process.returncode is not None:
                return "exec_session_exited"
            try:
                await process.handle.write(input_text)
            except (OSError, RuntimeError, ValueError):
                return "exec_stdin_closed"
            return None

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
                if isinstance(session.process, SidecarProcess):
                    await session.process.client.terminate(
                        session.process.process_id,
                        signal="terminate" if not expired else "kill",
                    )
                elif isinstance(session.process, _CapabilityProcess):
                    await session.process.terminate(force=expired)
                else:
                    await terminate_process_tree(
                        session.process,
                        force=expired,
                    )
                await wait_for_process(session.process, 1000)

            await self.finalize_if_exited(session)

            if session.finalized and (expired or idle):
                self.sessions.pop(session.session_id, None)
                self._notify_change()

    async def stop_running_sessions(
        self,
        *,
        session_ids: typing.Iterable[str] | None = None,
    ) -> dict[str, typing.Any]:
        """终止并回收指定或全部仍在运行的进程会话。"""
        await self.cleanup()

        selected_ids = (
            {
                str(session_id or "").strip()
                for session_id in session_ids
                if str(session_id or "").strip()
            }
            if session_ids is not None
            else None
        )
        sessions = [
            session
            for session in self.sessions.values()
            if session.process.returncode is None
               and (
                   selected_ids is None
                   or session.session_id in selected_ids
               )
        ]
        stopped: list[dict[str, typing.Any]] = []
        failures: list[dict[str, typing.Any]] = []

        for session in sessions:
            item = {
                "session_id": session.session_id,
                "command": session.command,
                "pid": session.process.pid,
                "origin": session.origin,
            }
            try:
                if isinstance(session.process, SidecarProcess):
                    await session.process.client.terminate(
                        session.process.process_id,
                        signal="terminate",
                    )
                elif isinstance(session.process, _CapabilityProcess):
                    await session.process.terminate(force=False)
                else:
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
            self._notify_change()
            stopped.append({
                **item,
                "exit_code": session.process.returncode,
            })

        result = {
            "ok": not failures,
            "requested": len(sessions),
            "stopped": len(stopped),
            "failed": len(failures),
            "items": stopped,
            "failures": failures,
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
                if isinstance(session.process, SidecarProcess):
                    await session.process.client.terminate(
                        session.process.process_id,
                        signal="kill",
                    )
                elif isinstance(session.process, _CapabilityProcess):
                    await session.process.terminate(force=True)
                else:
                    await terminate_process_tree(session.process, force=True)
            await self.finalize_if_exited(session)

        self.sessions.clear()
        if self._sandbox_client is not None:
            await self._sandbox_client.close()
        if self._network_proxy is not None:
            await self._network_proxy.close()

    async def finalize_if_exited(self, session: ProcessSession) -> None:
        """在进程退出后收束输出读取任务。"""
        if session.finalized or session.process.returncode is None:
            return None

        if isinstance(session.process, _CapabilityProcess):
            try:
                await session.process.handle.write("", eof=True)
            except (OSError, RuntimeError, ValueError):
                pass
        else:
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

        if isinstance(session.process, _CapabilityProcess):
            await session.process.close()
        elif not isinstance(session.process, SidecarProcess):
            ProcessCapture.close_process_transport(session.process)

        if session.network_proxy is not None:
            await session.network_proxy.close()

        session.finalized = True
        session.output_revision += 1
        session.update_event.set()
        self._notify_change()

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
        if isinstance(session.process, _CapabilityProcess):
            stream_reader = (
                session.process.handle.read_stdout
                if name == "stdout"
                else session.process.handle.read_stderr
            )
            async for text in stream_reader():
                chunk = str(text).encode("utf-8", errors="replace")
                await self._record_output(session, name, chunk)
            return None

        stream = session.process.stdout if name == "stdout" else session.process.stderr

        if stream is None:
            return None

        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return None
            await self._record_output(session, name, chunk)

    async def _record_output(
        self,
        session: ProcessSession,
        name: str,
        chunk: bytes,
    ) -> None:
        """把任一进程后端的输出写入统一会话缓冲。"""
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
            session.output_revision += 1
            session.output_events.append((
                session.output_revision,
                name,
                bytes(chunk),
            ))
            session.update_event.set()
            self._notify_change()

    async def _watch_process_exit(self, session: ProcessSession) -> None:
        """在本地进程退出时发出一次会话更新事件。"""
        await session.process.wait()
        if not session.finalized:
            session.output_revision += 1
            session.update_event.set()
            self._notify_change()

    @staticmethod
    def _clip(value: str, limit: int) -> str:
        """按字符上限截断输出文本。"""
        if len(value) <= limit:
            return value
        return f"{value[:limit]}\n...[truncated {len(value) - limit} chars]"


if __name__ == '__main__':
    pass
