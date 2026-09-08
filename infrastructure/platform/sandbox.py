# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import base64
import json
import os
import sys
import typing
from pathlib import Path

from infrastructure.config.paths import ApplicationLayout


SandboxFailureStage = typing.Literal[
    "startup",
    "handshake",
    "request",
    "spawn",
    "control",
]


class SandboxError(RuntimeError):
    """保存 Sandbox 边界失败的稳定分类和后端诊断事实。"""

    def __init__(
        self,
        code: str,
        backend_code: str,
        detail: str,
        *,
        stage: SandboxFailureStage,
        retryable: bool,
    ) -> None:
        self.code = code
        self.backend_code = backend_code
        self.detail = detail
        self.stage = stage
        self.retryable = retryable
        message = backend_code
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


class SandboxUnavailable(SandboxError):
    """表示当前平台的本地沙箱 sidecar 不可用。"""

    def __init__(
        self,
        detail: str,
        *,
        backend_code: str = "sandbox_unavailable",
        stage: SandboxFailureStage = "startup",
        retryable: bool = True,
    ) -> None:
        super().__init__(
            "sandbox_unavailable",
            backend_code,
            detail,
            stage=stage,
            retryable=retryable,
        )


class SandboxProtocolError(SandboxError):
    """表示 sidecar 返回了协议或执行错误。"""

    def __init__(
        self,
        backend_code: str,
        detail: str = "",
        *,
        stage: SandboxFailureStage | None = None,
        retryable: bool | None = None,
    ) -> None:
        code, default_stage, default_retryable = _protocol_failure_mapping(
            backend_code
        )
        super().__init__(
            code,
            backend_code,
            detail,
            stage=default_stage if stage is None else stage,
            retryable=(
                default_retryable if retryable is None else retryable
            ),
        )


def _protocol_failure_mapping(
    backend_code: str,
) -> tuple[str, SandboxFailureStage, bool]:
    """把 v1 后端错误码映射为稳定的本地失败语义。"""
    if backend_code == "sandbox_spawn_failed":
        return "sandbox_process_start_failed", "spawn", False
    if backend_code in {
        "cwd_outside_workspace_roots",
        "sandbox_mode_disabled",
        "argv_empty",
    }:
        return "sandbox_request_invalid", "request", False
    return "sandbox_protocol_error", "request", False


_PLATFORM_DIRECTORIES = {
    "win32": "windows",
    "darwin": "macos",
}

_EXECUTABLE_NAMES = {
    "win32": ("mind_sandbox_server.exe",),
    "darwin": ("mind_sandbox_server",),
}

_TTY_INTERRUPT_INPUT = b"\x03"
_POSIX_TTY_EOF_INPUT = b"\x04"
_WINDOWS_TTY_EOF_INPUT = b"\x1a\r"


def sandbox_platform_name(platform: str | None = None) -> str:
    """返回当前平台对应的沙箱目录名。"""
    value = (sys.platform if platform is None else platform).strip().lower()
    return _PLATFORM_DIRECTORIES.get(value, value or "unsupported")


def sandbox_backend_name(platform: str | None = None) -> str:
    """返回当前平台的 sidecar 后端标识。"""
    return f"{sandbox_platform_name(platform)}-sidecar"


def sandbox_executable_path(layout: ApplicationLayout) -> Path:
    """根据应用布局返回当前平台唯一的 Sandbox Sidecar 路径。"""
    names = _EXECUTABLE_NAMES.get(
        layout.platform,
        ("mind_sandbox_server",),
    )
    return (
        layout.root
        / "schematic"
        / "sandbox"
        / sandbox_platform_name(layout.platform)
        / "bin"
        / names[0]
    ).resolve()


class _SidecarStream(object):
    """把 sidecar 的事件流适配成 asyncio StreamReader 风格。"""

    def __init__(self) -> None:
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._buffer = bytearray()
        self._closed = False

    def feed(self, chunk: bytes) -> None:
        if self._closed:
            return
        self._chunks.put_nowait(bytes(chunk))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._chunks.put_nowait(None)

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            while True:
                chunk = await self._chunks.get()
                if chunk is None:
                    result = bytes(self._buffer)
                    self._buffer.clear()
                    return result
                self._buffer.extend(chunk)

        requested = max(1, int(size or 1))
        while not self._buffer:
            chunk = await self._chunks.get()
            if chunk is None:
                return b""
            self._buffer.extend(chunk)

        if len(self._buffer) <= requested:
            result = bytes(self._buffer)
            self._buffer.clear()
            return result

        result = bytes(self._buffer[:requested])
        del self._buffer[:requested]
        return result


class _SidecarStdin(object):
    """把同步 write/drain 调用转换为 sidecar 的异步写请求。"""

    def __init__(self, client: "SandboxClient", process_id: str) -> None:
        self._client = client
        self._process_id = process_id
        self._pending: bytearray = bytearray()
        self._closing: bool = False
        self._close_task: asyncio.Task[None] | None = None

    def is_closing(self) -> bool:
        return self._closing

    def write(self, data: bytes) -> None:
        if self._closing:
            raise RuntimeError("sidecar stdin is closed")
        self._pending.extend(bytes(data))

    async def drain(self) -> None:
        if self._pending:
            data = bytes(self._pending)
            self._pending.clear()
            await self._client.write(self._process_id, data=data)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        pending = bytes(self._pending)
        self._pending.clear()
        self._close_task = asyncio.create_task(
            self._client.write(
                self._process_id,
                data=pending,
                eof=True,
            )
        )

    async def wait_closed(self) -> None:
        if self._close_task is not None:
            await asyncio.gather(self._close_task, return_exceptions=True)


class SidecarProcess(object):
    """表示由 sidecar 管理的逻辑进程。"""

    def __init__(self, client: "SandboxClient", process_id: str) -> None:
        self.client = client
        self.process_id = process_id
        self.pid = process_id
        self.returncode: int | None = None
        self.stdin = _SidecarStdin(client, process_id)
        self.stdout = _SidecarStream()
        self.stderr = _SidecarStream()
        self._exit_event: asyncio.Event = asyncio.Event()

    def feed_output(self, stream: str, data: bytes) -> None:
        target = self.stdout if stream == "stdout" else self.stderr
        target.feed(data)

    def finish(self, exit_code: int) -> None:
        if self.returncode is not None:
            return
        self.returncode = int(exit_code)
        self.stdout.close()
        self.stderr.close()
        self._exit_event.set()

    async def wait(self) -> int:
        await self._exit_event.wait()
        return int(self.returncode if self.returncode is not None else -1)


class SandboxClient(object):
    """管理当前平台的 sandbox sidecar 及其逻辑进程。"""

    PROTOCOL_VERSION = 1
    REQUEST_TIMEOUT_SEC = 30.0
    READY_TIMEOUT_SEC = 10.0

    def __init__(
        self,
        *,
        workspace_root: str | os.PathLike[str],
        executable: str | os.PathLike[str],
        platform: str,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.platform = platform.strip().lower()
        self.platform_name = sandbox_platform_name(self.platform)
        self.executable = Path(executable).expanduser().resolve()

        self._sidecar: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._start_lock: asyncio.Lock = asyncio.Lock()
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._request_number: int = 0
        self._pending: dict[str, asyncio.Future[dict[str, typing.Any]]] = {}
        self._ready: asyncio.Future[bool] | None = None
        self._processes: dict[str, SidecarProcess] = {}
        self._early_events: dict[str, list[dict[str, typing.Any]]] = {}

    @property
    def available(self) -> bool:
        return self.executable.is_file()

    def _handle_process_event(self, event: dict[str, typing.Any]) -> None:
        process_id = str(event.get("process_id") or "").strip()
        if not process_id:
            return
        process = self._processes.get(process_id)
        if process is None:
            self._early_events.setdefault(process_id, []).append(dict(event))
            return

        event_name = str(event.get("event") or "").strip()
        if event_name in {"stdout", "stderr"}:
            try:
                data = base64.b64decode(str(event.get("data") or ""))
            except (ValueError, TypeError):
                return
            process.feed_output(event_name, data)
            return
        if event_name == "exit":
            try:
                exit_code = int(event.get("exit_code"))
            except (TypeError, ValueError):
                exit_code = -1
            process.finish(exit_code)

    async def _request(
        self,
        method: str,
        params: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        await self.ensure_started()
        process = self._sidecar
        if process is None or process.stdin is None:
            raise SandboxUnavailable(
                "sandbox sidecar stdin is unavailable",
                backend_code="sidecar_transport_unavailable",
                stage="request",
            )

        self._request_number += 1
        request_id = f"r{self._request_number}"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {
            "id": request_id,
            "method": str(method),
            "params": dict(params),
        }
        try:
            try:
                async with self._write_lock:
                    process.stdin.write(
                        (json.dumps(payload, ensure_ascii=True) + "\n").encode()
                    )
                    await process.stdin.drain()
            except OSError as exc:
                raise SandboxUnavailable(
                    str(exc).strip() or type(exc).__name__,
                    backend_code="sidecar_transport_failed",
                    stage="request",
                ) from exc
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=self.REQUEST_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError as exc:
                raise SandboxProtocolError(
                    "sidecar_request_timeout",
                    f"request {method!r} did not receive a response",
                    stage="request",
                    retryable=True,
                ) from exc
        finally:
            self._pending.pop(request_id, None)

        if not isinstance(response, dict):
            raise SandboxProtocolError(
                "sidecar_response_invalid",
                "response must be an object",
            )
        ok = response.get("ok")
        if not isinstance(ok, bool):
            raise SandboxProtocolError(
                "sidecar_response_invalid",
                "response field 'ok' must be a boolean",
            )
        if not ok:
            error = response.get("error")
            if not isinstance(error, dict):
                raise SandboxProtocolError(
                    "sidecar_response_invalid",
                    "failed response field 'error' must be an object",
                )
            code = error.get("code")
            if not isinstance(code, str) or not code.strip():
                raise SandboxProtocolError(
                    "sidecar_response_invalid",
                    "failed response field 'error.code' must be a non-empty string",
                )
            detail_value = error.get("detail", "")
            if detail_value is None:
                detail = ""
            elif isinstance(detail_value, str):
                detail = detail_value.strip()
            else:
                raise SandboxProtocolError(
                    "sidecar_response_invalid",
                    "failed response field 'error.detail' must be a string or null",
                )
            raise SandboxProtocolError(code.strip(), detail)
        result = response.get("result")
        if result is None and "result" not in response:
            return {}
        if not isinstance(result, dict):
            raise SandboxProtocolError(
                "sidecar_response_invalid",
                "successful response field 'result' must be an object",
            )
        return result

    async def _read_events(self) -> None:
        process = self._sidecar
        if process is None or process.stdout is None:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                event = str(message.get("event") or "").strip()
                if event == "ready":
                    protocol_value = message.get("protocol_version")
                    protocol_version = (
                        protocol_value
                        if isinstance(protocol_value, int)
                        and not isinstance(protocol_value, bool)
                        else None
                    )
                    if protocol_version != self.PROTOCOL_VERSION:
                        error = SandboxUnavailable(
                            "unsupported sandbox sidecar protocol version: "
                            f"{protocol_value!r}",
                            backend_code="sidecar_protocol_version_mismatch",
                            stage="handshake",
                            retryable=False,
                        )
                        if self._ready is not None and not self._ready.done():
                            self._ready.set_exception(error)
                        return
                    if self._ready is not None and not self._ready.done():
                        self._ready.set_result(True)
                    continue
                if event in {"stdout", "stderr", "exit"}:
                    self._handle_process_event(message)
                    continue
                request_id = str(message.get("id") or "").strip()
                future = self._pending.get(request_id)
                if future is not None and not future.done():
                    future.set_result(message)
        finally:
            stage: SandboxFailureStage = "request"
            if self._ready is not None and not self._ready.done():
                stage = "handshake"
            error = SandboxUnavailable(
                "sandbox sidecar exited",
                backend_code="sidecar_exited",
                stage=stage,
            )
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(error)
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            for process in tuple(self._processes.values()):
                process.finish(-1)

    async def _abort_sidecar(self) -> None:
        reader_task = self._reader_task
        self._reader_task = None
        if reader_task is not None and reader_task is not asyncio.current_task():
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)

        process = self._sidecar
        self._sidecar = None
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            await asyncio.gather(process.wait(), return_exceptions=True)

    async def ensure_started(self) -> None:
        if self._sidecar is not None and self._sidecar.returncode is None:
            return
        async with self._start_lock:
            if self._sidecar is not None and self._sidecar.returncode is None:
                return
            if not self.available:
                raise SandboxUnavailable(
                    (
                        "sandbox sidecar not found: "
                        f"{self.executable} (platform={self.platform_name})"
                    ),
                    backend_code="sidecar_not_found",
                    retryable=False,
                )

            try:
                self._sidecar = await asyncio.create_subprocess_exec(
                    str(self.executable),
                    cwd=str(self.workspace_root),
                    env=os.environ.copy(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                raise SandboxUnavailable(
                    str(exc).strip() or type(exc).__name__,
                    backend_code="sidecar_start_failed",
                    stage="startup",
                    retryable=False,
                ) from exc
            self._ready = asyncio.get_running_loop().create_future()
            self._reader_task = asyncio.create_task(
                self._read_events(),
                name="mind sandbox sidecar events",
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._ready),
                    timeout=self.READY_TIMEOUT_SEC,
                )
            except (SandboxUnavailable, asyncio.TimeoutError) as exc:
                await self._abort_sidecar()
                if isinstance(exc, SandboxUnavailable):
                    raise
                raise SandboxUnavailable(
                    "sandbox sidecar did not become ready",
                    backend_code="sidecar_ready_timeout",
                    stage="handshake",
                ) from exc

    async def spawn(
        self,
        *,
        argv: typing.Sequence[str],
        cwd: str | os.PathLike[str],
        env: dict[str, str],
        sandbox_mode: str,
        stdin_open: bool,
        tty: bool = False,
        timeout_ms: int | None = None,
        additional_permissions: dict[str, typing.Any] | None = None,
    ) -> SidecarProcess:
        await self.ensure_started()
        params: dict[str, typing.Any] = {
            "argv": [str(item) for item in argv],
            "cwd": str(Path(cwd).resolve()),
            "workspace_roots": [str(self.workspace_root)],
            "mode": str(sandbox_mode),
            "env": {str(key): str(value) for key, value in env.items()},
            "stdin_open": bool(stdin_open),
            "tty": bool(tty),
            "timeout_ms": timeout_ms,
        }
        if self.platform == "win32":
            params["level"] = "restricted-token"
        if additional_permissions is not None:
            params["additional_permissions"] = dict(additional_permissions)
        response = await self._request("spawn", params)

        process_id = str(response.get("process_id") or "").strip()
        if not process_id:
            raise SandboxProtocolError(
                "sidecar_response_invalid",
                "spawn result field 'process_id' must be a non-empty string",
                stage="spawn",
            )

        process = SidecarProcess(self, process_id)
        self._processes[process_id] = process
        for event in self._early_events.pop(process_id, []):
            self._handle_process_event(event)
        return process

    async def write(
        self,
        process_id: str,
        *,
        data: bytes = b"",
        eof: bool = False,
    ) -> None:
        await self._request(
            "write",
            {
                "process_id": str(process_id),
                "data": base64.b64encode(data).decode("ascii"),
                "eof": bool(eof),
            },
        )

    async def terminate(self, process_id: str, *, signal: str = "terminate") -> None:
        try:
            await self._request(
                "terminate",
                {"process_id": str(process_id), "signal": str(signal)},
            )
        except SandboxProtocolError:
            process = self._processes.get(str(process_id))
            if process is not None and process.returncode is None:
                process.finish(-1)
            raise

    async def interrupt(self, process_id: str, *, tty: bool) -> None:
        """按进程类型发送中断；PTY 使用终端输入语义。"""
        if tty:
            await self.write(process_id, data=_TTY_INTERRUPT_INPUT)
            return
        await self.terminate(process_id, signal="interrupt")

    async def close_input(self, process_id: str, *, tty: bool) -> None:
        """按进程类型关闭输入；PTY 发送平台对应的终端 EOF。"""
        if not tty:
            await self.write(process_id, eof=True)
            return
        eof_input = (
            _WINDOWS_TTY_EOF_INPUT
            if self.platform == "win32"
            else _POSIX_TTY_EOF_INPUT
        )
        await self.write(process_id, data=eof_input)

    async def close(self) -> None:
        if self._sidecar is None:
            return
        try:
            if self._sidecar.returncode is None:
                await self._request("close", {})
        except SandboxError:
            pass
        await self._abort_sidecar()


if __name__ == '__main__':
    pass
