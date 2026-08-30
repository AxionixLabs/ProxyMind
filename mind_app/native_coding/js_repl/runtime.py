# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import json
import uuid
import shutil
import typing
import asyncio
import tempfile
import contextlib
from collections import deque
from dataclasses import (
    dataclass,
    field
)
from pathlib import Path
from infrastructure.config.paths import resolve_application_layout
from metadata import const

ToolCallback = typing.Callable[
    [str, dict[str, typing.Any], str],
    typing.Awaitable[dict[str, typing.Any]]
]

MIN_NODE_VERSION   = (22, 22, 0)
DEFAULT_TIMEOUT_MS = 30_000

STDERR_TAIL_LINE_LIMIT  = 20
STDERR_TAIL_LINE_BYTES  = 512
STDERR_TAIL_MAX_BYTES   = 4_096
STDERR_TAIL_SEPARATOR   = " | "
STDOUT_READ_CHUNK_BYTES = 64 * 1024
STDOUT_FRAME_MAX_BYTES  = 32 * 1024 * 1024

SUPPORTED_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}

REPL_ACCESS_MODES = {
    "read-only",
    "workspace-write",
    "danger-full-access",
}


def _asset_root() -> Path:
    """返回 JavaScript 内核静态资源目录。"""
    source_root = Path(__file__).resolve().parents[3]
    try:
        application_root = resolve_application_layout(
            entry_file=source_root / "mind.py"
        ).root
    except ValueError:
        application_root = source_root
    return application_root / "js_repl"


class ReplRuntimeError(RuntimeError):
    """描述 JavaScript 内核运行失败。"""


@dataclass(frozen=True, slots=True)
class ReplExecution:
    """描述一次 JavaScript 单元执行结果。"""
    output: str
    attachments: tuple[dict[str, typing.Any], ...] = ()


@dataclass(slots=True)
class _ExecutionContext:
    """保存单个执行请求使用的工具回调和附件。"""
    call_tool: ToolCallback
    attachments: list[dict[str, typing.Any]] = field(default_factory=list)
    request_tasks: set[asyncio.Task[None]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _ExecutionRequest:
    """保存执行请求的完成信号和运行上下文。"""
    request_id: str
    future: asyncio.Future[dict[str, typing.Any]]
    context: _ExecutionContext


def _parse_node_version(value: str) -> tuple[int, int, int]:
    """解析 Node 版本文本。"""
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(value or "").strip())
    if match is None:
        raise ReplRuntimeError(f"Unable to parse Node version: {value!r}")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _image_attachment(image_url: str, detail: typing.Any) -> dict[str, typing.Any]:
    """校验 data URL 并构造图片附件。"""
    if not isinstance(image_url, str) or not image_url.lower().startswith("data:"):
        raise ReplRuntimeError("host.emitImage only accepts data URLs")

    header, separator, _ = image_url.partition(",")

    mime_type = header[5:].split(";", 1)[0].lower() if separator else ""
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise ReplRuntimeError(
            "host.emitImage only supports image/png, image/jpeg, image/webp, or image/gif"
        )

    attachment: dict[str, typing.Any] = {
        "kind": "image",
        "mime_type": mime_type,
        "data_url": image_url,
        "detail": detail or "high",
    }

    if detail is not None and detail not in {"auto", "low", "high", "original"}:
        raise ReplRuntimeError("host.emitImage received an invalid image detail")
    return attachment


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """按 UTF-8 字节上限截断文本并保留完整字符。"""
    encoded = value.encode(const.CHARSET)
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode(const.CHARSET, errors="ignore")


def _stderr_tail_bytes(lines: deque[str]) -> int:
    """返回标准错误尾部按分隔符连接后的字节数。"""
    if not lines:
        return 0
    payload = sum(len(line.encode(const.CHARSET)) for line in lines)
    separators = len(STDERR_TAIL_SEPARATOR.encode(const.CHARSET)) * (len(lines) - 1)
    return payload + separators


def _append_stderr_tail(lines: deque[str], value: str) -> None:
    """追加一行有界标准错误并优先保留最新诊断。"""
    bounded = _truncate_utf8(value, STDERR_TAIL_LINE_BYTES)
    if not bounded:
        return

    while lines and (
        len(lines) >= STDERR_TAIL_LINE_LIMIT
        or _stderr_tail_bytes(lines)
        + len(STDERR_TAIL_SEPARATOR.encode(const.CHARSET))
        + len(bounded.encode(const.CHARSET))
        > STDERR_TAIL_MAX_BYTES
    ):
        lines.popleft()
    lines.append(bounded)


class JavaScriptReplManager:
    """维护单个会话的持久 JavaScript 内核。"""

    def __init__(self, *, cwd: Path, session_id: str, access_mode: str) -> None:
        """保存工作目录和内核会话标识。"""
        if access_mode not in REPL_ACCESS_MODES:
            raise ReplRuntimeError(f"unsupported js_repl access mode: {access_mode}")

        self.cwd         = cwd.resolve()
        self.session_id  = session_id
        self.access_mode = access_mode
        self._temp_dir   = tempfile.TemporaryDirectory(prefix="js-repl-")

        self._process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task[None] | None     = None
        self._stderr_task: asyncio.Task[None] | None     = None

        self._stdin_lock: asyncio.Lock = asyncio.Lock()
        self._exec_lock: asyncio.Lock  = asyncio.Lock()

        self._active_request: _ExecutionRequest | None = None

        self._stderr_tail: deque[str] = deque()

        self._closed: bool = False

    async def execute(
        self,
        code: str,
        *,
        timeout_ms: int,
        call_tool: ToolCallback
    ) -> ReplExecution:
        """在持久内核中串行执行一个 JavaScript 单元。"""
        if self._closed:
            raise ReplRuntimeError("js_repl runtime is closed")

        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 0:
            raise ReplRuntimeError("js_repl timeout_ms must be a non-negative integer")

        timeout = timeout_ms

        async with self._exec_lock:
            if self._closed:
                raise ReplRuntimeError("js_repl runtime is closed")

            try:
                await self._ensure_kernel()
            except asyncio.CancelledError:
                await asyncio.shield(self._reset_kernel())
                raise

            request = _ExecutionRequest(
                request_id=str(uuid.uuid4()),
                future=asyncio.get_running_loop().create_future(),
                context=_ExecutionContext(call_tool=call_tool),
            )
            self._active_request = request

            try:
                try:
                    await self._write_message({
                        "type": "exec",
                        "id": request.request_id,
                        "code": code,
                        "timeout_ms": timeout,
                    })
                    message = await asyncio.wait_for(
                        asyncio.shield(request.future),
                        timeout / 1000,
                    )
                except asyncio.TimeoutError as exc:
                    request.future.cancel()
                    await self._reset_kernel()
                    raise ReplRuntimeError(
                        "js_repl execution timed out; kernel reset, rerun your request"
                    ) from exc
                except (BrokenPipeError, ConnectionError, OSError) as exc:
                    request.future.cancel()
                    await self._reset_kernel()
                    raise ReplRuntimeError(
                        f"failed to communicate with js_repl kernel: {exc}"
                    ) from exc
            except asyncio.CancelledError:
                request.future.cancel()
                await asyncio.shield(self._reset_kernel())
                raise
            finally:
                if self._active_request is request:
                    self._active_request = None
                await self._cancel_request_tasks(request.context)

            if not bool(message.get("ok")):
                error = str(message.get("error") or "js_repl execution failed")
                raise ReplRuntimeError(error)

            return ReplExecution(
                output=str(message.get("output") or ""),
                attachments=tuple(request.context.attachments),
            )

    async def close(self) -> None:
        """关闭内核并释放临时目录。"""
        if self._closed:
            return
        self._closed = True
        await self._reset_kernel()
        self._temp_dir.cleanup()

    async def reset(self) -> None:
        """重置内核并保留可按需复用的会话管理器。"""
        async with self._exec_lock:
            if self._closed:
                raise ReplRuntimeError("js_repl runtime is closed")
            await self._reset_kernel()

    async def _ensure_kernel(self) -> None:
        """在需要时启动并校验 Node 内核。"""
        process = self._process
        if process is not None and process.returncode is None:
            return

        node_path   = await self._resolve_node_path()
        asset_root  = _asset_root()
        kernel_path = asset_root / "kernel.js"

        meriyah_path = asset_root / "vendor" / "meriyah.umd.min.js"
        if not kernel_path.is_file() or not meriyah_path.is_file():
            raise ReplRuntimeError(
                f"js_repl runtime assets are missing from {asset_root}"
            )

        env = dict(os.environ)
        env["JS_REPL_SESSION_ID"] = self.session_id
        env["JS_REPL_TMP_DIR"]    = self._temp_dir.name

        if not str(env.get("HOME") or "").strip():
            env["HOME"] = str(Path.home())

        self._stderr_tail.clear()

        process = await asyncio.create_subprocess_exec(
            node_path,
            *self._node_arguments(),
            str(kernel_path),
            cwd=str(self.cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._process = process

        self._stdout_task = asyncio.create_task(
            self._read_stdout(process),
            name="js repl stdout",
        )
        self._stderr_task = asyncio.create_task(
            self._read_stderr(process),
            name="js repl stderr",
        )

    def _node_arguments(self) -> tuple[str, ...]:
        """返回与当前访问模式匹配的 Node 启动参数。"""
        arguments = ["--experimental-vm-modules"]
        if self.access_mode == "danger-full-access":
            return tuple(arguments)

        arguments[0:0] = [
            "--permission",
            "--allow-fs-read=*",
            f"--allow-fs-write={self._temp_dir.name}",
        ]
        if self.access_mode == "workspace-write":
            arguments.insert(3, f"--allow-fs-write={self.cwd}")
        return tuple(arguments)

    @staticmethod
    async def _resolve_node_path() -> str:
        """解析满足最低版本要求的 Node 可执行文件。"""
        configured      = str(os.environ.get("JS_REPL_NODE_PATH") or "").strip()
        configured_path = Path(configured).expanduser() if configured else None

        node_path = (
            str(configured_path.resolve())
            if configured_path is not None and configured_path.is_file()
            else shutil.which("node")
        )

        if not node_path:
            raise ReplRuntimeError(
                "Node runtime not found; install Node or set JS_REPL_NODE_PATH"
            )

        process = await asyncio.create_subprocess_exec(
            node_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise ReplRuntimeError(f"failed to read Node version: {detail}")

        version_text = stdout.decode(errors="replace").strip()

        found = _parse_node_version(version_text)
        if found < MIN_NODE_VERSION:
            required = ".".join(str(part) for part in MIN_NODE_VERSION)
            raise ReplRuntimeError(
                f"Node runtime too old for js_repl: found {version_text}, requires >= v{required}"
            )
        return node_path

    async def _write_message(self, message: dict[str, typing.Any]) -> None:
        """向内核写入一条 JSONL 消息。"""
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise BrokenPipeError("js_repl kernel is unavailable")

        payload = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode(const.CHARSET) + b"\n"

        async with self._stdin_lock:
            process.stdin.write(payload)
            await process.stdin.drain()

    async def _read_stdout(self, process: asyncio.subprocess.Process) -> None:
        """读取内核消息并分派执行、工具和图片事件。"""
        stream = process.stdout
        if stream is None:
            return

        frame_buffer = bytearray()

        try:
            while True:
                chunk = await stream.read(STDOUT_READ_CHUNK_BYTES)
                if not chunk:
                    break

                frame_buffer.extend(chunk)
                while True:
                    separator = frame_buffer.find(b"\n")
                    if separator < 0:
                        if len(frame_buffer) > STDOUT_FRAME_MAX_BYTES:
                            await self._fail_pending(
                                "js_repl stdout frame exceeded "
                                f"{STDOUT_FRAME_MAX_BYTES} bytes; kernel reset"
                            )
                            return
                        break

                    frame = bytes(frame_buffer[:separator])
                    del frame_buffer[:separator + 1]
                    if not frame:
                        continue

                    if len(frame) > STDOUT_FRAME_MAX_BYTES:
                        await self._fail_pending(
                            "js_repl stdout frame exceeded "
                            f"{STDOUT_FRAME_MAX_BYTES} bytes; kernel reset"
                        )
                        return

                    try:
                        message = json.loads(frame)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    await self._dispatch_message(message)

        except asyncio.CancelledError:
            raise

        finally:
            if self._process is process:
                self._process = None
                request = self._active_request
                if request is not None:
                    await self._wait_for_request_tasks(request.context)
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                        await asyncio.wait_for(process.wait(), timeout=2)
                await self._fail_pending(self._kernel_exit_error(process))

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        """保留内核标准错误的有限尾部。"""
        stream = process.stderr
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if text:
                _append_stderr_tail(self._stderr_tail, text)

    async def _dispatch_message(self, message: typing.Any) -> None:
        """按消息类型处理内核输出。"""
        if not isinstance(message, dict):
            return

        message_type = message.get("type")
        if message_type == "exec_result":
            exec_id = str(message.get("id") or "")
            request = self._active_request
            if request is not None and request.request_id == exec_id:
                await self._wait_for_request_tasks(request.context)
                if not request.future.done():
                    request.future.set_result(message)
            return

        if message_type not in {"run_tool", "emit_image"}:
            return

        exec_id = str(message.get("exec_id") or "")
        request = self._active_request

        if request is None or request.request_id != exec_id:
            await self._reply_missing_context(message)
            return

        context = request.context

        if message_type == "run_tool":
            task = asyncio.create_task(
                self._handle_tool_request(message, context)
            )
        else:
            task = asyncio.create_task(
                self._handle_image_request(message, context)
            )
        context.request_tasks.add(task)
        task.add_done_callback(context.request_tasks.discard)

    async def _handle_tool_request(
        self,
        message: dict[str, typing.Any],
        context: _ExecutionContext
    ) -> None:
        """执行内核发起的嵌套工具调用。"""
        request_id = str(message.get("id") or "")
        tool_name  = str(message.get("tool_name") or "").strip()

        try:
            if tool_name in {"js_repl", "js_repl_reset"}:
                raise ReplRuntimeError("js_repl cannot invoke itself")

            arguments = json.loads(str(message.get("arguments") or "{}"))

            if not isinstance(arguments, dict):
                raise ReplRuntimeError("host.tool arguments must be an object")

            response = await context.call_tool(tool_name, arguments, request_id)

            payload = {
                "type": "run_tool_result",
                "id": request_id,
                "ok": True,
                "response": response,
                "error": None,
            }

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            payload = {
                "type": "run_tool_result",
                "id": request_id,
                "ok": False,
                "response": None,
                "error": str(exc).strip() or type(exc).__name__,
            }

        with contextlib.suppress(BrokenPipeError, ConnectionError, OSError):
            await self._write_message(payload)

    async def _handle_image_request(
        self,
        message: dict[str, typing.Any],
        context: _ExecutionContext,
    ) -> None:
        """记录内核显式发出的图片附件。"""
        request_id = str(message.get("id") or "")
        try:
            attachment = _image_attachment(
                message.get("image_url"),
                message.get("detail"),
            )
            context.attachments.append(attachment)
            payload = {
                "type": "emit_image_result",
                "id": request_id,
                "ok": True,
                "error": None,
            }
        except Exception as exc:
            payload = {
                "type": "emit_image_result",
                "id": request_id,
                "ok": False,
                "error": str(exc).strip() or type(exc).__name__,
            }
        with contextlib.suppress(BrokenPipeError, ConnectionError, OSError):
            await self._write_message(payload)

    async def _reply_missing_context(self, message: dict[str, typing.Any]) -> None:
        """回复已经失效的执行上下文请求。"""
        response_type = (
            "run_tool_result"
            if message.get("type") == "run_tool"
            else "emit_image_result"
        )
        with contextlib.suppress(BrokenPipeError, ConnectionError, OSError):
            await self._write_message({
                "type": response_type,
                "id": str(message.get("id") or ""),
                "ok": False,
                "error": "js_repl exec context not found",
            })

    async def _reset_kernel(self) -> None:
        """终止当前内核并取消关联请求。"""
        process = self._process
        self._process = None

        request = self._active_request
        if request is not None:
            await self._cancel_request_tasks(request.context)
        await self._fail_pending("js_repl kernel reset")

        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                await asyncio.wait_for(process.wait(), timeout=2)

        current = asyncio.current_task()

        tasks: list[asyncio.Task[None]] = []

        stdout_task = self._stdout_task
        stderr_task = self._stderr_task

        if stdout_task is not None and stdout_task is not current:
            tasks.append(stdout_task)
        if stderr_task is not None and stderr_task is not current:
            tasks.append(stderr_task)
        self._stdout_task = None
        self._stderr_task = None

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fail_pending(self, message: str) -> None:
        """使所有等待中的执行请求以错误结束。"""
        request = self._active_request
        if request is not None and not request.future.done():
            request.future.set_exception(ReplRuntimeError(message))

    @staticmethod
    async def _cancel_request_tasks(context: _ExecutionContext) -> None:
        """取消并回收一个执行请求的后台任务。"""
        tasks = tuple(context.request_tasks)
        context.request_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _wait_for_request_tasks(context: _ExecutionContext) -> None:
        """等待本次单元已经发起的未等待桥接调用完成。"""
        while context.request_tasks:
            tasks = tuple(context.request_tasks)
            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )
            context.request_tasks.difference_update(tasks)

    def _kernel_exit_error(self, process: asyncio.subprocess.Process) -> str:
        """生成包含有限诊断信息的内核退出错误。"""
        details = " | ".join(self._stderr_tail)
        message = f"js_repl kernel exited unexpectedly (exit_code={process.returncode})"
        return f"{message}: {details}" if details else message


class JavaScriptReplPool:
    """按对话会话隔离并管理持久内核。"""

    def __init__(self, root: str | Path) -> None:
        """保存默认工作区并初始化内核表。"""
        self.root = Path(root).resolve()

        self._sessions: dict[str, JavaScriptReplManager] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def execute(
        self,
        session_id: str,
        code: str,
        *,
        cwd: str | Path | None,
        timeout_ms: int,
        call_tool: ToolCallback,
        access_mode: str = "workspace-write",
    ) -> ReplExecution:
        """在指定会话的内核中运行代码。"""
        key = str(session_id or "").strip()
        if not key:
            raise ReplRuntimeError("js_repl session id is required")
        if access_mode not in REPL_ACCESS_MODES:
            raise ReplRuntimeError(f"unsupported js_repl access mode: {access_mode}")
        target_cwd = Path(cwd or self.root).resolve()

        async with self._lock:
            manager = self._sessions[key] if key in self._sessions else None
            if manager is not None and (
                manager.cwd != target_cwd
                or manager.access_mode != access_mode
            ):
                await manager.close()
                manager = None
            if manager is None:
                manager = JavaScriptReplManager(
                    cwd=target_cwd,
                    session_id=key,
                    access_mode=access_mode,
                )
                self._sessions[key] = manager

        return await manager.execute(
            code,
            timeout_ms=timeout_ms,
            call_tool=call_tool,
        )

    async def reset_session(self, session_id: str) -> bool:
        """重置指定会话已经初始化的内核。"""
        key = str(session_id or "").strip()
        if not key:
            raise ReplRuntimeError("js_repl session id is required")
        async with self._lock:
            manager = self._sessions[key] if key in self._sessions else None
        if manager is None:
            return False
        await manager.reset()
        return True

    async def close_session(self, session_id: str) -> bool:
        """关闭并移除指定会话的内核管理器。"""
        key = str(session_id or "").strip()
        if not key:
            return False
        async with self._lock:
            if key not in self._sessions:
                return False
            manager = self._sessions[key]
            del self._sessions[key]
        await manager.close()
        return True

    async def close(self) -> None:
        """关闭池中全部内核。"""
        async with self._lock:
            managers = tuple(
                self._sessions[key]
                for key in tuple(self._sessions)
            )
            self._sessions.clear()
        if managers:
            await asyncio.gather(
                *(manager.close() for manager in managers),
                return_exceptions=True,
            )


if __name__ == '__main__':
    pass
