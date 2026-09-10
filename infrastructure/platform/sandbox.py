# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import base64
import binascii
import collections
import dataclasses
import json
import os
import subprocess
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
_FutureResult = typing.TypeVar("_FutureResult")
_JsonValue: typing.TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | list["_JsonValue"]
    | dict[str, "_JsonValue"]
)


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


class SandboxOutcomeUnknown(SandboxError):
    """表示 Sidecar 失联后无法证明执行是否产生了副作用。"""

    def __init__(
        self,
        detail: str,
        *,
        backend_code: str = "sidecar_exited",
        stage: SandboxFailureStage = "request",
    ) -> None:
        super().__init__(
            "execution_outcome_unknown",
            backend_code,
            detail,
            stage=stage,
            retryable=False,
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


@dataclasses.dataclass(frozen=True, slots=True)
class _ProcessEvent:
    """保存已在 JSONL 边界验证的进程事件。"""

    name: typing.Literal["stdout", "stderr", "exit"]
    process_id: str
    data: bytes = b""
    exit_code: int | None = None

    @property
    def retained_bytes(self) -> int:
        """返回事件占用早到缓存的有效载荷字节数。"""
        return len(self.data)


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

    def __init__(
        self,
        client: "SandboxClient",
        process_id: str,
        generation: int,
    ) -> None:
        self._client = client
        self._process_id = process_id
        self._generation = generation
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
            await self._client.write(
                self._process_id,
                data=data,
                generation=self._generation,
            )

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
                generation=self._generation,
            )
        )

    async def wait_closed(self) -> None:
        if self._close_task is not None:
            await asyncio.gather(self._close_task, return_exceptions=True)

    def finish(self) -> None:
        """在逻辑进程退出时丢弃待写数据且不再发送协议请求。"""
        self._closing = True
        self._pending.clear()


class SidecarProcess(object):
    """表示由 sidecar 管理的逻辑进程。"""

    def __init__(
        self,
        client: "SandboxClient",
        process_id: str,
        *,
        generation: int | None = None,
    ) -> None:
        self.client = client
        self.process_id = process_id
        self.generation = (
            client.generation if generation is None else int(generation)
        )
        self.pid = process_id
        self.returncode: int | None = None
        self.execution_outcome_unknown = False
        self.stdin = _SidecarStdin(client, process_id, self.generation)
        self.stdout = _SidecarStream()
        self.stderr = _SidecarStream()
        self._exit_event: asyncio.Event = asyncio.Event()

    def feed_output(self, stream: str, data: bytes) -> None:
        target = self.stdout if stream == "stdout" else self.stderr
        target.feed(data)

    def finish(
        self,
        exit_code: int,
        *,
        outcome_unknown: bool = False,
    ) -> None:
        if self.returncode is not None:
            return
        self.returncode = int(exit_code)
        self.execution_outcome_unknown = bool(outcome_unknown)
        self.stdin.finish()
        self.stdout.close()
        self.stderr.close()
        self._exit_event.set()
        self.client._release_process(self)

    async def wait(self) -> int:
        await self._exit_event.wait()
        return int(self.returncode if self.returncode is not None else -1)


class SandboxClient(object):
    """管理当前平台的 sandbox sidecar 及其逻辑进程。"""

    PROTOCOL_VERSION = 1
    REQUEST_TIMEOUT_SEC = 30.0
    READY_TIMEOUT_SEC = 10.0
    ABORT_WAIT_TIMEOUT_SEC = 2.0
    FRAME_LIMIT_BYTES = 1024 * 1024
    STDERR_TAIL_LIMIT_BYTES = 16 * 1024
    EARLY_PROCESS_LIMIT = 64
    EARLY_EVENT_LIMIT = 256
    EARLY_BYTES_LIMIT = 1024 * 1024
    COMPLETED_PROCESS_LIMIT = 256

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
        self._stderr_task: asyncio.Task[None] | None = None
        self._start_lock: asyncio.Lock = asyncio.Lock()
        self._abort_lock: asyncio.Lock = asyncio.Lock()
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._generation: int = 0
        self._sidecar_generation: int | None = None
        self._transport_failed: bool = False
        self._transport_error: SandboxError | None = None
        self._expected_shutdown_generations: set[int] = set()
        self._request_number: int = 0
        self._pending: dict[str, asyncio.Future[dict[str, typing.Any]]] = {}
        self._pending_methods: dict[str, str] = {}
        self._ready: asyncio.Future[bool] | None = None
        self._processes: dict[tuple[int, str], SidecarProcess] = {}
        self._early_events: dict[tuple[int, str], list[_ProcessEvent]] = {}
        self._early_event_count: int = 0
        self._early_event_bytes: int = 0
        self._completed_processes: collections.deque[tuple[int, str]] = (
            collections.deque()
        )
        self._completed_process_set: set[tuple[int, str]] = set()
        self._stderr_tail: bytearray = bytearray()

    @property
    def available(self) -> bool:
        return self.executable.is_file()

    @property
    def generation(self) -> int:
        """返回当前 Sidecar 的本地生命周期代次。"""
        return int(self._sidecar_generation or self._generation)

    @staticmethod
    def _consume_future_exception(
        future: asyncio.Future[_FutureResult],
    ) -> None:
        """消费后台 future 的异常，避免事件循环产生未检索告警。"""
        if future.cancelled():
            return
        future.exception()

    def _transport_running(self) -> bool:
        """判断当前 Sidecar 协议通道是否仍可接受请求。"""
        process = self._sidecar
        reader_task = self._reader_task
        return bool(
            process is not None
            and process.returncode is None
            and self._sidecar_generation is not None
            and not self._transport_failed
            and reader_task is not None
            and not reader_task.done()
        )

    @staticmethod
    def _process_key(generation: int, process_id: str) -> tuple[int, str]:
        """返回隔离 Sidecar 重启代次的逻辑进程身份。"""
        return int(generation), str(process_id)

    def _remember_completed_process(self, key: tuple[int, str]) -> None:
        """有界保存已退出进程身份，用于忽略重复或迟到事件。"""
        if key in self._completed_process_set:
            return
        while len(self._completed_processes) >= self.COMPLETED_PROCESS_LIMIT:
            expired = self._completed_processes.popleft()
            self._completed_process_set.discard(expired)
        self._completed_processes.append(key)
        self._completed_process_set.add(key)

    def _release_process(self, process: SidecarProcess) -> None:
        """在逻辑进程退出时立即释放客户端进程表记录。"""
        key = self._process_key(process.generation, process.process_id)
        if self._processes.get(key) is not process:
            return
        self._processes.pop(key, None)
        self._remember_completed_process(key)

    def _pop_early_events(self, key: tuple[int, str]) -> list[_ProcessEvent]:
        """移除指定进程的早到事件并归还全部缓存预算。"""
        events = self._early_events.pop(key, [])
        self._early_event_count -= len(events)
        self._early_event_bytes -= sum(
            event.retained_bytes for event in events
        )
        return events

    def _cache_early_event(
        self,
        key: tuple[int, str],
        event: _ProcessEvent,
    ) -> None:
        """在固定进程数、事件数和字节预算内缓存乱序事件。"""
        new_process = key not in self._early_events
        if new_process and len(self._early_events) >= self.EARLY_PROCESS_LIMIT:
            raise SandboxProtocolError(
                "sidecar_early_event_limit",
                "early process event cache exceeded its process limit",
            )
        if self._early_event_count >= self.EARLY_EVENT_LIMIT:
            raise SandboxProtocolError(
                "sidecar_early_event_limit",
                "early process event cache exceeded its event limit",
            )
        retained_bytes = event.retained_bytes
        if self._early_event_bytes + retained_bytes > self.EARLY_BYTES_LIMIT:
            raise SandboxProtocolError(
                "sidecar_early_event_limit",
                "early process event cache exceeded its byte limit",
            )
        self._early_events.setdefault(key, []).append(event)
        self._early_event_count += 1
        self._early_event_bytes += retained_bytes

    def _handle_process_event(
        self,
        event: _ProcessEvent,
        *,
        generation: int,
    ) -> None:
        key = self._process_key(generation, event.process_id)
        if key in self._completed_process_set:
            return
        process = self._processes.get(key)
        if process is None:
            self._cache_early_event(key, event)
            return

        if event.name in {"stdout", "stderr"}:
            process.feed_output(event.name, event.data)
            return
        exit_code = event.exit_code
        if exit_code is None:
            raise SandboxProtocolError(
                "sidecar_event_invalid",
                "exit event is missing a validated exit code",
            )
        process.finish(exit_code)

    async def _request(
        self,
        method: str,
        params: dict[str, typing.Any],
        *,
        generation: int | None = None,
    ) -> dict[str, typing.Any]:
        if generation is not None and (
            self._sidecar_generation != int(generation)
            or self._transport_failed
        ):
            if method == "spawn":
                raise SandboxUnavailable(
                    "sandbox sidecar generation changed before spawn request",
                    backend_code="sidecar_transport_unavailable",
                    stage="spawn",
                )
            raise SandboxOutcomeUnknown(
                "logical process belongs to an expired sidecar generation",
                backend_code="sidecar_generation_expired",
                stage=self._request_stage(method),
            )
        await self.ensure_started()
        process = self._sidecar
        active_generation = self._sidecar_generation
        if (
            process is None
            or process.stdin is None
            or active_generation is None
            or self._transport_failed
        ):
            raise SandboxUnavailable(
                "sandbox sidecar stdin is unavailable",
                backend_code="sidecar_transport_unavailable",
                stage=self._request_stage(method),
            )
        if generation is not None and int(generation) != active_generation:
            raise SandboxOutcomeUnknown(
                "logical process belongs to an expired sidecar generation",
                backend_code="sidecar_generation_expired",
                stage="control",
            )

        self._request_number += 1
        request_id = f"g{active_generation}:r{self._request_number}"
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(self._consume_future_exception)
        self._pending[request_id] = future
        self._pending_methods[request_id] = str(method)
        payload = {
            "id": request_id,
            "method": str(method),
            "params": dict(params),
        }
        try:
            if (
                self._sidecar_generation != active_generation
                or self._transport_failed
            ):
                raise SandboxUnavailable(
                    "sandbox sidecar transport stopped before request write",
                    backend_code="sidecar_transport_unavailable",
                    stage=self._request_stage(method),
                )
            try:
                async with self._write_lock:
                    process.stdin.write(
                        (json.dumps(payload, ensure_ascii=True) + "\n").encode()
                    )
                    await process.stdin.drain()
            except OSError as exc:
                detail = str(exc).strip() or type(exc).__name__
                transport_error = SandboxUnavailable(
                    detail,
                    backend_code="sidecar_transport_failed",
                    stage=self._request_stage(method),
                )
                self._settle_generation(
                    active_generation,
                    transport_error,
                    outcome_unknown=True,
                )
                await self._abort_sidecar(error=transport_error)
                if self._request_can_apply(method):
                    raise SandboxOutcomeUnknown(
                        detail,
                        backend_code="sidecar_transport_failed",
                        stage=self._request_stage(method),
                    ) from exc
                raise transport_error from exc
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=self.REQUEST_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError as exc:
                if self._request_can_apply(method):
                    failure = SandboxOutcomeUnknown(
                        f"request {method!r} did not receive a response",
                        backend_code="sidecar_request_timeout",
                        stage=self._request_stage(method),
                    )
                    self._settle_generation(
                        active_generation,
                        failure,
                        outcome_unknown=True,
                    )
                    await self._abort_sidecar(error=failure)
                    raise failure from exc
                raise SandboxProtocolError(
                    "sidecar_request_timeout",
                    f"request {method!r} did not receive a response",
                    stage=self._request_stage(method),
                    retryable=True,
                ) from exc
        finally:
            self._pending.pop(request_id, None)
            self._pending_methods.pop(request_id, None)
            if not future.done():
                future.cancel()

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

    def _protocol_error(
        self,
        backend_code: str,
        detail: str,
    ) -> SandboxProtocolError:
        """按当前握手状态创建严格协议失败。"""
        stage: SandboxFailureStage = "request"
        if self._ready is not None and not self._ready.done():
            stage = "handshake"
        return SandboxProtocolError(
            backend_code,
            detail,
            stage=stage,
            retryable=False,
        )

    def _decode_frame(self, line: bytes) -> dict[str, _JsonValue]:
        """校验 JSONL 单帧字节预算、编码和顶层类型。"""
        if len(line) > self.FRAME_LIMIT_BYTES:
            raise self._protocol_error(
                "sidecar_frame_too_large",
                f"sidecar frame exceeds {self.FRAME_LIMIT_BYTES} bytes",
            )
        try:
            decoded = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise self._protocol_error(
                "sidecar_frame_invalid_utf8",
                "sidecar frame is not valid UTF-8",
            ) from exc
        try:
            message = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise self._protocol_error(
                "sidecar_frame_invalid_json",
                "sidecar frame is not valid JSON",
            ) from exc
        if not isinstance(message, dict):
            raise self._protocol_error(
                "sidecar_frame_invalid",
                "sidecar frame must contain an object",
            )
        return message

    def _decode_process_event(
        self,
        message: dict[str, _JsonValue],
        event_name: str,
    ) -> _ProcessEvent:
        """把进程事件转换为已验证且大小可计量的本地值。"""
        process_id_value = message.get("process_id")
        if (
            not isinstance(process_id_value, str)
            or not process_id_value.strip()
        ):
            raise self._protocol_error(
                "sidecar_event_invalid",
                f"{event_name} event field 'process_id' must be a non-empty string",
            )
        process_id = process_id_value.strip()
        if event_name in {"stdout", "stderr"}:
            data_value = message.get("data")
            if not isinstance(data_value, str):
                raise self._protocol_error(
                    "sidecar_event_invalid",
                    f"{event_name} event field 'data' must be a base64 string",
                )
            try:
                data = base64.b64decode(data_value, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise self._protocol_error(
                    "sidecar_event_invalid",
                    f"{event_name} event field 'data' is not valid base64",
                ) from exc
            if event_name == "stdout":
                return _ProcessEvent(
                    name="stdout",
                    process_id=process_id,
                    data=data,
                )
            return _ProcessEvent(
                name="stderr",
                process_id=process_id,
                data=data,
            )

        exit_code = message.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise self._protocol_error(
                "sidecar_event_invalid",
                "exit event field 'exit_code' must be an integer",
            )
        return _ProcessEvent(
            name="exit",
            process_id=process_id,
            exit_code=exit_code,
        )

    @staticmethod
    def _append_bounded_tail(
        tail: bytearray,
        chunk: bytes,
        *,
        limit_bytes: int,
    ) -> None:
        """把诊断字节追加到固定大小的尾部窗口。"""
        tail.extend(chunk)
        overflow = len(tail) - max(1, int(limit_bytes))
        if overflow > 0:
            del tail[:overflow]

    async def _read_sidecar_stderr(
        self,
        process: asyncio.subprocess.Process,
        tail: bytearray,
    ) -> None:
        """持续消费 Sidecar stderr 并只保留有限诊断尾部。"""
        if process.stderr is None:
            return
        try:
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    return
                self._append_bounded_tail(
                    tail,
                    chunk,
                    limit_bytes=self.STDERR_TAIL_LIMIT_BYTES,
                )
        except asyncio.CancelledError:
            return
        except (OSError, RuntimeError) as exc:
            diagnostic = (
                str(exc).strip() or type(exc).__name__
            ).encode("utf-8", errors="replace")
            self._append_bounded_tail(
                tail,
                diagnostic,
                limit_bytes=self.STDERR_TAIL_LIMIT_BYTES,
            )

    @staticmethod
    def _with_stderr_tail(detail: str, tail: bytearray) -> str:
        """在可用时为生命周期失败附加有限 stderr 诊断。"""
        stderr_tail = bytes(tail).decode("utf-8", errors="replace").strip()
        if not stderr_tail:
            return detail
        return f"{detail}; sidecar stderr tail: {stderr_tail}"

    @staticmethod
    def _request_stage(method: str) -> SandboxFailureStage:
        """返回请求方法对应的本地执行阶段。"""
        if method == "spawn":
            return "spawn"
        if method in {"write", "terminate"}:
            return "control"
        return "request"

    @staticmethod
    def _request_can_apply(method: str) -> bool:
        """判断请求在失去响应时是否可能已经产生本地副作用。"""
        return method in {"spawn", "write", "terminate"}

    def _settle_generation(
        self,
        generation: int,
        error: SandboxError,
        *,
        outcome_unknown: bool,
    ) -> None:
        """原子收束指定 Sidecar 代次的 future、进程和缓存。"""
        if generation != self._sidecar_generation:
            return
        self._transport_failed = True
        self._transport_error = error
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(error)

        request_prefix = f"g{generation}:"
        for request_id, future in tuple(self._pending.items()):
            if not request_id.startswith(request_prefix) or future.done():
                continue
            request_error = error
            method = self._pending_methods.get(request_id, "")
            if outcome_unknown and self._request_can_apply(method):
                request_error = SandboxOutcomeUnknown(
                    error.detail,
                    backend_code=error.backend_code,
                    stage=self._request_stage(method),
                )
            future.set_exception(request_error)

        for key, process in tuple(self._processes.items()):
            if key[0] == generation:
                process.finish(-1, outcome_unknown=outcome_unknown)
        for key in tuple(self._early_events):
            if key[0] == generation:
                self._pop_early_events(key)

    async def _read_events(
        self,
        process: asyncio.subprocess.Process,
        generation: int,
        stderr_tail: bytearray,
    ) -> None:
        """读取并严格分派一个 Sidecar generation 的 JSONL 帧。"""
        failure: SandboxError | None = None
        ready_received = False
        if process.stdout is None:
            failure = SandboxUnavailable(
                "sandbox sidecar stdout is unavailable",
                backend_code="sidecar_transport_unavailable",
                stage="handshake",
            )
        try:
            while failure is None:
                if (
                    generation != self._sidecar_generation
                    or process is not self._sidecar
                ):
                    return
                try:
                    line = await process.stdout.readline()
                except ValueError as exc:
                    raise self._protocol_error(
                        "sidecar_frame_too_large",
                        f"sidecar frame exceeds {self.FRAME_LIMIT_BYTES} bytes",
                    ) from exc
                if not line:
                    await asyncio.sleep(0)
                    stage: SandboxFailureStage = (
                        "request" if ready_received else "handshake"
                    )
                    failure = SandboxUnavailable(
                        self._with_stderr_tail(
                            "sandbox sidecar exited",
                            stderr_tail,
                        ),
                        backend_code="sidecar_exited",
                        stage=stage,
                    )
                    break

                message = self._decode_frame(line)
                event_value = message.get("event")
                if event_value is not None and not isinstance(event_value, str):
                    raise self._protocol_error(
                        "sidecar_event_invalid",
                        "sidecar frame field 'event' must be a string",
                    )
                event_name = str(event_value or "").strip()
                if event_name == "ready":
                    if ready_received:
                        raise self._protocol_error(
                            "sidecar_event_duplicate",
                            "sidecar emitted more than one ready event",
                        )
                    protocol_value = message.get("protocol_version")
                    protocol_version = (
                        protocol_value
                        if isinstance(protocol_value, int)
                        and not isinstance(protocol_value, bool)
                        else None
                    )
                    if protocol_version != self.PROTOCOL_VERSION:
                        raise SandboxUnavailable(
                            "unsupported sandbox sidecar protocol version: "
                            f"{protocol_value!r}",
                            backend_code="sidecar_protocol_version_mismatch",
                            stage="handshake",
                            retryable=False,
                        )
                    ready_received = True
                    if self._ready is not None and not self._ready.done():
                        self._ready.set_result(True)
                    continue
                if event_name in {"stdout", "stderr", "exit"}:
                    self._handle_process_event(
                        self._decode_process_event(message, event_name),
                        generation=generation,
                    )
                    continue
                if event_name and event_name != "response":
                    raise self._protocol_error(
                        "sidecar_event_unknown",
                        f"unknown sidecar event: {event_name}",
                    )

                request_id_value = message.get("id")
                if (
                    not isinstance(request_id_value, str)
                    or not request_id_value.strip()
                ):
                    raise self._protocol_error(
                        "sidecar_response_invalid",
                        "response field 'id' must be a non-empty string",
                    )
                request_id = request_id_value.strip()
                if not request_id.startswith(f"g{generation}:"):
                    continue
                future = self._pending.get(request_id)
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            if generation not in self._expected_shutdown_generations:
                failure = SandboxUnavailable(
                    "sandbox sidecar event reader was cancelled",
                    backend_code="sidecar_reader_cancelled",
                    stage="request" if ready_received else "handshake",
                )
        except SandboxError as exc:
            failure = exc
        except (OSError, RuntimeError) as exc:
            failure = SandboxUnavailable(
                self._with_stderr_tail(
                    str(exc).strip() or type(exc).__name__,
                    stderr_tail,
                ),
                backend_code="sidecar_transport_failed",
                stage="request" if ready_received else "handshake",
            )
        finally:
            if failure is not None:
                self._settle_generation(
                    generation,
                    failure,
                    outcome_unknown=(
                        ready_received
                        and generation not in self._expected_shutdown_generations
                    ),
                )
                await self._abort_sidecar(error=failure)

    async def _wait_for_sidecar_exit(
        self,
        process: asyncio.subprocess.Process,
    ) -> None:
        """有界终止 Sidecar，终止请求失败时继续等待和强制终止。"""
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except (OSError, RuntimeError):
            pass
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=self.ABORT_WAIT_TIMEOUT_SEC,
            )
            return
        except (asyncio.TimeoutError, OSError, RuntimeError):
            pass
        try:
            process.kill()
        except (OSError, RuntimeError):
            return
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=self.ABORT_WAIT_TIMEOUT_SEC,
            )
        except (asyncio.TimeoutError, OSError, RuntimeError):
            return

    async def _abort_sidecar(
        self,
        *,
        error: SandboxError | None = None,
    ) -> None:
        """尽力执行全部 Sidecar 清理步骤并清空本地生命周期状态。"""
        async with self._abort_lock:
            await self._abort_sidecar_locked(error=error)

    async def _abort_sidecar_locked(
        self,
        *,
        error: SandboxError | None = None,
    ) -> None:
        """在清理互斥内收束当前 Sidecar 的全部资源。"""
        process = self._sidecar
        generation = self._sidecar_generation
        reader_task = self._reader_task
        stderr_task = self._stderr_task
        if generation is not None:
            self._expected_shutdown_generations.add(generation)
            shutdown_error = error or SandboxUnavailable(
                "sandbox sidecar stopped",
                backend_code="sidecar_stopped",
                stage="request",
                retryable=False,
            )
            self._settle_generation(
                generation,
                shutdown_error,
                outcome_unknown=False,
            )

        tasks = [
            task
            for task in (reader_task, stderr_task)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if process is not None:
            await self._wait_for_sidecar_exit(process)

        if self._sidecar is process:
            self._sidecar = None
            self._sidecar_generation = None
            self._reader_task = None
            self._stderr_task = None
            self._transport_failed = False
        if generation is not None:
            self._expected_shutdown_generations.discard(generation)
            retained = collections.deque(
                key for key in self._completed_processes if key[0] != generation
            )
            self._completed_processes = retained
            self._completed_process_set = set(retained)

    async def ensure_started(self) -> None:
        if self._transport_running():
            return
        async with self._start_lock:
            if self._transport_running():
                return
            if self._sidecar is not None:
                await self._abort_sidecar(error=self._transport_error)
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
                process = await asyncio.create_subprocess_exec(
                    str(self.executable),
                    cwd=str(self.workspace_root),
                    env=os.environ.copy(),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    # 子进程可另开 CONOUT$ 或 /dev/tty；标准流捕获之外还需解除父终端继承。
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    start_new_session=os.name == "posix",
                    limit=self.FRAME_LIMIT_BYTES + 1,
                )
            except OSError as exc:
                raise SandboxUnavailable(
                    str(exc).strip() or type(exc).__name__,
                    backend_code="sidecar_start_failed",
                    stage="startup",
                    retryable=False,
                ) from exc
            self._generation += 1
            generation = self._generation
            self._sidecar = process
            self._sidecar_generation = generation
            self._transport_failed = False
            self._transport_error = None
            self._request_number = 0
            self._stderr_tail = bytearray()
            self._ready = asyncio.get_running_loop().create_future()
            self._ready.add_done_callback(self._consume_future_exception)
            self._stderr_task = asyncio.create_task(
                self._read_sidecar_stderr(process, self._stderr_tail),
                name=f"mind sandbox sidecar stderr g{generation}",
            )
            self._reader_task = asyncio.create_task(
                self._read_events(process, generation, self._stderr_tail),
                name=f"mind sandbox sidecar events g{generation}",
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._ready),
                    timeout=self.READY_TIMEOUT_SEC,
                )
                if not self._transport_running():
                    raise self._transport_error or SandboxUnavailable(
                        "sandbox sidecar transport stopped after handshake",
                        backend_code="sidecar_exited",
                        stage="handshake",
                    )
            except SandboxError as exc:
                await self._abort_sidecar(error=exc)
                raise
            except asyncio.TimeoutError as exc:
                error = SandboxUnavailable(
                    self._with_stderr_tail(
                        "sandbox sidecar did not become ready",
                        self._stderr_tail,
                    ),
                    backend_code="sidecar_ready_timeout",
                    stage="handshake",
                )
                await self._abort_sidecar(error=error)
                raise error from exc

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
        generation = self._sidecar_generation
        if generation is None:
            raise SandboxUnavailable(
                "sandbox sidecar generation is unavailable",
                backend_code="sidecar_transport_unavailable",
                stage="spawn",
            )
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
        response = await self._request(
            "spawn",
            params,
            generation=generation,
        )

        process_id_value = response.get("process_id")
        if (
            not isinstance(process_id_value, str)
            or not process_id_value.strip()
        ):
            raise SandboxProtocolError(
                "sidecar_response_invalid",
                "spawn result field 'process_id' must be a non-empty string",
                stage="spawn",
            )
        process_id = process_id_value.strip()

        key = self._process_key(generation, process_id)
        if key in self._completed_process_set or key in self._processes:
            raise SandboxProtocolError(
                "sidecar_process_id_reused",
                "spawn result reused a process id within one sidecar generation",
                stage="spawn",
            )
        process = SidecarProcess(
            self,
            process_id,
            generation=generation,
        )
        self._processes[key] = process
        for event in self._pop_early_events(key):
            self._handle_process_event(event, generation=generation)
        return process

    async def write(
        self,
        process_id: str,
        *,
        data: bytes = b"",
        eof: bool = False,
        generation: int | None = None,
    ) -> None:
        await self._request(
            "write",
            {
                "process_id": str(process_id),
                "data": base64.b64encode(data).decode("ascii"),
                "eof": bool(eof),
            },
            generation=generation,
        )

    async def terminate(
        self,
        process_id: str,
        *,
        signal: str = "terminate",
        generation: int | None = None,
    ) -> None:
        await self._request(
            "terminate",
            {"process_id": str(process_id), "signal": str(signal)},
            generation=generation,
        )

    async def interrupt(
        self,
        process_id: str,
        *,
        tty: bool,
        generation: int | None = None,
    ) -> None:
        """按进程类型发送中断；PTY 使用终端输入语义。"""
        if tty:
            await self.write(
                process_id,
                data=_TTY_INTERRUPT_INPUT,
                generation=generation,
            )
            return
        await self.terminate(
            process_id,
            signal="interrupt",
            generation=generation,
        )

    async def close_input(
        self,
        process_id: str,
        *,
        tty: bool,
        generation: int | None = None,
    ) -> None:
        """按进程类型关闭输入；PTY 发送平台对应的终端 EOF。"""
        if not tty:
            await self.write(
                process_id,
                eof=True,
                generation=generation,
            )
            return
        eof_input = (
            _WINDOWS_TTY_EOF_INPUT
            if self.platform == "win32"
            else _POSIX_TTY_EOF_INPUT
        )
        await self.write(
            process_id,
            data=eof_input,
            generation=generation,
        )

    async def close(self) -> None:
        if self._sidecar is None:
            return
        generation = self._sidecar_generation
        if generation is not None:
            self._expected_shutdown_generations.add(generation)
        try:
            if self._transport_running():
                await self._request("close", {})
        except SandboxError:
            pass
        finally:
            await self._abort_sidecar()


if __name__ == '__main__':
    pass
