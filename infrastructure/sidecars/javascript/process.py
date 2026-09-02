# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
from collections import deque
from pathlib import Path

from agent.ports.capabilities import SandboxMode
from agent.ports.javascript import (
    JavaScriptExecutionError,
    JavaScriptFailureKind,
)
from infrastructure.sidecars.javascript.bundle import JavaScriptBundle
from infrastructure.sidecars.javascript.protocol import (
    FRAME_MAX_BYTES,
    KernelMessage,
    parse_kernel_frame,
)
from metadata import const

MIN_NODE_VERSION = (22, 22, 0)
STDERR_TAIL_LINE_LIMIT = 20
STDERR_TAIL_LINE_BYTES = 512
STDERR_TAIL_MAX_BYTES = 4_096
STDERR_TAIL_SEPARATOR = " | "
STDOUT_READ_CHUNK_BYTES = 64 * 1024


class JavaScriptSidecarProcess:
    """持有一个安全信封内的 Node 进程、传输流和临时目录。"""

    def __init__(
        self,
        *,
        cwd: Path,
        session_id: str,
        access_mode: SandboxMode,
        bundle: JavaScriptBundle,
        configured_node_path: str | None = None,
    ) -> None:
        """固定进程级工作目录、权限和运行时资产。"""
        self.cwd = cwd.resolve()
        self.session_id = session_id
        self.access_mode = access_mode
        self.bundle = bundle
        self.configured_node_path = configured_node_path
        self._temp_dir = tempfile.TemporaryDirectory(prefix="js-repl-")
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stdin_lock = asyncio.Lock()
        self._frame_buffer = bytearray()
        self._stderr_tail: deque[str] = deque()
        self._resolved_node_path: str | None = None
        self._closed = False

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        """返回当前进程句柄供 Session 判断存活状态。"""
        return self._process

    @property
    def running(self) -> bool:
        """返回当前 Node 进程是否仍在运行。"""
        process = self._process
        return process is not None and process.returncode is None

    @property
    def stderr_detail(self) -> str:
        """返回有界标准错误尾部。"""
        return STDERR_TAIL_SEPARATOR.join(self._stderr_tail)

    @property
    def temp_path(self) -> Path:
        """返回当前 Session 独占的临时目录。"""
        return Path(self._temp_dir.name)

    async def start(self) -> None:
        """校验 bundle 和 Node 后按不可变安全信封启动 Kernel。"""
        if self._closed:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                "JavaScript sidecar process is closed",
            )
        if self.running:
            return

        self.bundle.verify()
        node_path = await self._node_path()
        environment = dict(os.environ)
        environment["JS_REPL_SESSION_ID"] = self.session_id
        environment["JS_REPL_TMP_DIR"] = self._temp_dir.name
        if not str(environment.get("HOME") or "").strip():
            environment["HOME"] = str(Path.home())

        self._stderr_tail.clear()
        self._frame_buffer.clear()
        try:
            process = await asyncio.create_subprocess_exec(
                node_path,
                *self.node_arguments(),
                str(self.bundle.kernel_path),
                cwd=str(self.cwd),
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                f"Failed to start JavaScript sidecar: {error}",
            ) from error

        self._process = process
        self._stderr_task = asyncio.create_task(
            self._read_stderr(process),
            name="javascript sidecar stderr",
        )

    def node_arguments(self) -> tuple[str, ...]:
        """返回与固定 Sandbox 模式匹配的 Node 启动参数。"""
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

    async def write(self, payload: bytes) -> None:
        """向当前 Kernel 写入一条已编码消息。"""
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                "JavaScript sidecar kernel is unavailable",
            )
        try:
            async with self._stdin_lock:
                process.stdin.write(payload)
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionError, OSError) as error:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                f"Failed to communicate with JavaScript sidecar: {error}",
            ) from error

    async def read(self) -> KernelMessage | None:
        """从 stdout 读取并严格解析下一条完整 Kernel 消息。"""
        process = self._process
        stream = process.stdout if process is not None else None
        if stream is None:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                "JavaScript sidecar stdout is unavailable",
            )

        while True:
            separator = self._frame_buffer.find(b"\n")
            if separator >= 0:
                frame = bytes(self._frame_buffer[:separator])
                del self._frame_buffer[:separator + 1]
                if not frame:
                    continue
                return parse_kernel_frame(frame)
            if len(self._frame_buffer) > FRAME_MAX_BYTES:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.PROTOCOL_ERROR,
                    f"JavaScript sidecar frame exceeded {FRAME_MAX_BYTES} bytes",
                )

            try:
                chunk = await stream.read(STDOUT_READ_CHUNK_BYTES)
            except (ConnectionError, OSError) as error:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.UNAVAILABLE,
                    f"Failed to read JavaScript sidecar output: {error}",
                ) from error
            if chunk:
                self._frame_buffer.extend(chunk)
                continue
            if self._frame_buffer:
                raise JavaScriptExecutionError(
                    JavaScriptFailureKind.PROTOCOL_ERROR,
                    "JavaScript sidecar closed stdout with a partial frame",
                )
            return None

    async def wait(self) -> int | None:
        """等待当前进程退出并返回退出码。"""
        process = self._process
        if process is None:
            return None
        return await process.wait()

    async def terminate(self) -> None:
        """强制终止 Kernel，同时保留 Session 临时目录供重启使用。"""
        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                await asyncio.wait_for(process.wait(), timeout=2)

        stderr_task = self._stderr_task
        self._stderr_task = None
        if stderr_task is not None and stderr_task is not asyncio.current_task():
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
        self._frame_buffer.clear()

    async def close(self) -> None:
        """终止进程并释放 Session 临时目录。"""
        if self._closed:
            return
        self._closed = True
        await self.terminate()
        self._temp_dir.cleanup()

    async def _node_path(self) -> str:
        """解析并缓存满足最低版本的 Node 可执行文件。"""
        if self._resolved_node_path is not None:
            return self._resolved_node_path
        node_path = await resolve_node_path(self.configured_node_path)
        self._resolved_node_path = node_path
        return node_path

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        """持续收集当前进程的有界标准错误尾部。"""
        stream = process.stderr
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if text:
                append_stderr_tail(self._stderr_tail, text)


async def resolve_node_path(configured_node_path: str | None) -> str:
    """解析并验证显式配置或 PATH 中的 Node。"""
    configured = str(configured_node_path or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                f"Configured Node runtime does not exist: {candidate}",
            )
        node_path = str(candidate.resolve())
    else:
        discovered = shutil.which("node")
        if not discovered:
            raise JavaScriptExecutionError(
                JavaScriptFailureKind.UNAVAILABLE,
                "Node runtime not found; install Node or configure JS_REPL_NODE_PATH",
            )
        node_path = discovered

    try:
        process = await asyncio.create_subprocess_exec(
            node_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except OSError as error:
        raise JavaScriptExecutionError(
            JavaScriptFailureKind.UNAVAILABLE,
            f"Failed to inspect Node runtime: {error}",
        ) from error
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        raise JavaScriptExecutionError(
            JavaScriptFailureKind.UNAVAILABLE,
            f"Failed to read Node version: {detail}",
        )

    version_text = stdout.decode(errors="replace").strip()
    found = parse_node_version(version_text)
    if found < MIN_NODE_VERSION:
        required = ".".join(str(part) for part in MIN_NODE_VERSION)
        raise JavaScriptExecutionError(
            JavaScriptFailureKind.UNAVAILABLE,
            "Node runtime too old for js_repl: "
            f"found {version_text}, requires >= v{required}",
        )
    return node_path


def parse_node_version(value: str) -> tuple[int, int, int]:
    """解析 Node 版本文本。"""
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(value or "").strip())
    if match is None:
        raise JavaScriptExecutionError(
            JavaScriptFailureKind.UNAVAILABLE,
            f"Unable to parse Node version: {value!r}",
        )
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def append_stderr_tail(lines: deque[str], value: str) -> None:
    """追加一行有界标准错误并优先保留最新诊断。"""
    bounded = _truncate_utf8(value, STDERR_TAIL_LINE_BYTES)
    if not bounded:
        return
    while lines and (
        len(lines) >= STDERR_TAIL_LINE_LIMIT
        or stderr_tail_bytes(lines)
        + len(STDERR_TAIL_SEPARATOR.encode(const.CHARSET))
        + len(bounded.encode(const.CHARSET))
        > STDERR_TAIL_MAX_BYTES
    ):
        lines.popleft()
    lines.append(bounded)


def stderr_tail_bytes(lines: deque[str]) -> int:
    """返回标准错误尾部按分隔符连接后的字节数。"""
    if not lines:
        return 0
    payload = sum(len(line.encode(const.CHARSET)) for line in lines)
    separators = len(STDERR_TAIL_SEPARATOR.encode(const.CHARSET)) * (len(lines) - 1)
    return payload + separators


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """按 UTF-8 字节上限截断文本并保留完整字符。"""
    encoded = value.encode(const.CHARSET)
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode(const.CHARSET, errors="ignore")


if __name__ == '__main__':
    pass
