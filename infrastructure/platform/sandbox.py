# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import base64
import json
import os
import sys
import typing
from pathlib import Path

from infrastructure.config.paths import is_packaged_executable


class SandboxUnavailable(RuntimeError):
    """表示当前平台的本地沙箱 sidecar 不可用。"""


class SandboxProtocolError(RuntimeError):
    """表示 sidecar 返回了协议或执行错误。"""


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

        self._pending = bytearray()
        self._closing = False

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

        self._exit_event = asyncio.Event()

    async def wait(self) -> int:
        await self._exit_event.wait()
        return int(self.returncode if self.returncode is not None else -1)

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


class SandboxClient(object):
    """管理当前平台的 sandbox sidecar 及其逻辑进程。"""

    PROTOCOL_VERSION = 1
    REQUEST_TIMEOUT_SEC = 30.0
    READY_TIMEOUT_SEC = 10.0

    def __init__(
        self,
        *,
        workspace_root: str | os.PathLike[str],
        executable: str | os.PathLike[str] | None = None,
        application_root: str | os.PathLike[str] | None = None,
        packaged: bool | None = None,
        platform: str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.platform = (sys.platform if platform is None else platform).strip().lower()
        self.platform_name = sandbox_platform_name(self.platform)
        self.packaged = self._is_packaged_runtime() if packaged is None else bool(packaged)

        self.application_root = (
            Path(application_root).expanduser().resolve()
            if application_root is not None
            else self._default_application_root()
        )

        self.executable = self._resolve_executable(executable)

        self._sidecar: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None

        self._start_lock: asyncio.Lock = asyncio.Lock()
        self._write_lock: asyncio.Lock = asyncio.Lock()

        self._request_number: int = 0

        self._pending: dict[str, asyncio.Future[dict[str, typing.Any]]] = {}
        self._ready: asyncio.Future[bool] | None = None
        self._processes: dict[str, SidecarProcess] = {}
        self._early_events: dict[str, list[dict[str, typing.Any]]] = {}

    @staticmethod
    def _is_packaged_runtime() -> bool:
        """判断当前进程是否由独立应用入口启动。"""
        if bool(getattr(sys, "frozen", False)):
            return True
        return is_packaged_executable(sys.executable)

    def _default_application_root(self) -> Path:
        """返回源码或打包入口对应的应用根目录。"""
        if self.packaged:
            return Path(sys.executable).expanduser().resolve().parent
        return Path(__file__).resolve().parents[3]

    def _resolve_executable(
        self,
        executable: str | os.PathLike[str] | None,
    ) -> Path:
        if executable is not None:
            return Path(executable).expanduser().resolve()

        configured = os.environ.get("MIND_SANDBOX_SERVER", "").strip()
        if configured:
            return Path(configured).expanduser().resolve()

        names = _EXECUTABLE_NAMES.get(
            self.platform,
            ("mind_sandbox_server",),
        )
        sandbox_bin = (
            self.application_root
            / "schematic"
            / "sandbox"
            / self.platform_name
            / "bin"
        )
        candidates = tuple(sandbox_bin / name for name in names)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    @property
    def available(self) -> bool:
        return self.executable.is_file()

    async def ensure_started(self) -> None:
        if self._sidecar is not None and self._sidecar.returncode is None:
            return
        async with self._start_lock:
            if self._sidecar is not None and self._sidecar.returncode is None:
                return
            if not self.available:
                raise SandboxUnavailable(
                    f"sandbox sidecar not found: {self.executable}"
                )

            self._sidecar = await asyncio.create_subprocess_exec(
                str(self.executable),
                cwd=str(self.workspace_root),
                env=os.environ.copy(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
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
            except (SandboxUnavailable, asyncio.TimeoutError, RuntimeError) as exc:
                await self._abort_sidecar()
                if isinstance(exc, SandboxUnavailable):
                    raise
                raise SandboxUnavailable("sandbox sidecar did not become ready") from exc

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
            raise SandboxProtocolError("sidecar spawn response missing process_id")

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
        except (OSError, RuntimeError, SandboxProtocolError, asyncio.TimeoutError):
            pass
        await self._abort_sidecar()

    async def _request(
        self,
        method: str,
        params: dict[str, typing.Any],
    ) -> dict[str, typing.Any]:
        await self.ensure_started()
        process = self._sidecar
        if process is None or process.stdin is None:
            raise SandboxUnavailable("sandbox sidecar stdin is unavailable")

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
            async with self._write_lock:
                process.stdin.write(
                    (json.dumps(payload, ensure_ascii=True) + "\n").encode()
                )
                await process.stdin.drain()
            response = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.REQUEST_TIMEOUT_SEC,
            )
        finally:
            self._pending.pop(request_id, None)

        if not bool(response.get("ok")):
            error = response.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "sidecar_error")
                detail = str(error.get("detail") or "").strip()
                raise SandboxProtocolError(
                    f"{code}: {detail}" if detail else code
                )
            raise SandboxProtocolError("sidecar_error")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

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
                    try:
                        protocol_version = int(message["protocol_version"])
                    except (KeyError, TypeError, ValueError):
                        protocol_version = None
                    if protocol_version != self.PROTOCOL_VERSION:
                        error = SandboxUnavailable(
                            "unsupported sandbox sidecar protocol version: "
                            f"{message.get('protocol_version')!r}"
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
            error = SandboxUnavailable("sandbox sidecar exited")
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(error)
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            for process in tuple(self._processes.values()):
                process.finish(-1)

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


if __name__ == '__main__':
    pass
