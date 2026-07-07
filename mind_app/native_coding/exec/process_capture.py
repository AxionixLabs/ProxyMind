# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import time
import signal
import typing
import asyncio
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapturedProcessResult(object):
    """记录一次本地进程捕获结果。"""
    exit_code: int
    stdout: bytes
    stderr: bytes
    output_lines: tuple[str, ...]
    stdout_dropped: int
    stderr_dropped: int
    timed_out: bool
    elapsed_ms: int


class _CaptureBuffer(object):
    """保存有限长度的进程输出尾部。"""

    def __init__(self, *, limit_bytes: int) -> None:
        self.limit_bytes = max(1, int(limit_bytes or 1))
        self.data        = bytearray()
        self.dropped     = 0

    def append(self, chunk: bytes) -> None:
        """追加输出并按限制丢弃头部。"""
        if not chunk:
            return None
        self.data.extend(chunk)
        if len(self.data) <= self.limit_bytes:
            return None

        overflow = len(self.data) - self.limit_bytes
        del self.data[:overflow]
        self.dropped += overflow

    def bytes(self) -> bytes:
        """返回当前输出尾部。"""
        return bytes(self.data)


class _OrderedOutputBuffer(object):
    """按接收顺序保存有限数量的输出行。"""

    def __init__(self, *, max_lines: int = 800, max_line_chars: int = 1000) -> None:
        self.max_lines        = max(1, int(max_lines or 1))
        self.max_line_chars   = max(20, int(max_line_chars or 20))
        self.lines: list[str] = []

        self.pending: dict[str, str] = {
            "stdout": "",
            "stderr": "",
        }
        self.lock = asyncio.Lock()

    async def append(self, stream: str, chunk: bytes) -> None:
        """追加输出 chunk 并记录完整行。"""
        if not chunk:
            return None

        text       = chunk.decode(errors="replace")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")

        async with self.lock:
            combined = self.pending.get(stream, "") + normalized
            if combined.endswith("\n"):
                complete = combined.split("\n")[:-1]
                self.pending[stream] = ""
            else:
                parts = combined.split("\n")
                complete = parts[:-1]
                self.pending[stream] = parts[-1] if parts else ""

            for line in complete:
                if line:
                    self.lines.append(self._clip_line(line))

            if len(self.lines) > self.max_lines:
                del self.lines[:-self.max_lines]

    async def snapshot(self) -> tuple[str, ...]:
        """返回包含未完成行的输出快照。"""
        async with self.lock:
            lines = list(self.lines)
            for stream in ("stdout", "stderr"):
                pending = self.pending.get(stream, "")
                if pending:
                    lines.append(self._clip_line(pending))
            return tuple(lines[-self.max_lines:])

    def _clip_line(self, value: str) -> str:
        text = str(value or "").rstrip()
        if len(text) <= self.max_line_chars:
            return text
        return f"{text[:max(0, self.max_line_chars - 3)]}..."


class ProcessCapture(object):
    """提供一次性进程输出捕获能力。"""

    CHUNK_BYTES          = 4096
    IO_DRAIN_TIMEOUT_SEC = 2.0
    TERMINATE_GRACE_SEC  = 0.5

    @classmethod
    async def run_shell(
        cls,
        command: str,
        *,
        shell: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int = 60,
        buffer_limit_bytes: int = 1_000_000
    ) -> CapturedProcessResult:
        """执行 shell 命令并捕获有限输出。"""
        started = time.perf_counter()

        process = await cls._start_shell(
            command,
            shell=shell,
            cwd=cwd,
            env=env
        )

        stdout_buffer = _CaptureBuffer(limit_bytes=buffer_limit_bytes)
        stderr_buffer = _CaptureBuffer(limit_bytes=buffer_limit_bytes)
        output_buffer = _OrderedOutputBuffer()

        stdout_task = asyncio.create_task(
            cls._read_stream(process.stdout, stdout_buffer, output_buffer, "stdout")
        )
        stderr_task = asyncio.create_task(
            cls._read_stream(process.stderr, stderr_buffer, output_buffer, "stderr")
        )

        timed_out = False

        try:
            await asyncio.wait_for(process.wait(), timeout=max(1, int(timeout_sec or 1)))
        except asyncio.TimeoutError:
            timed_out = True
            await cls.terminate_process_tree(process, force=True)
        finally:
            await cls._drain_readers(process, [stdout_task, stderr_task])

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        exit_code = process.returncode
        if exit_code is None:
            exit_code = -1

        return CapturedProcessResult(
            exit_code=int(exit_code),
            stdout=stdout_buffer.bytes(),
            stderr=stderr_buffer.bytes(),
            output_lines=await output_buffer.snapshot(),
            stdout_dropped=stdout_buffer.dropped,
            stderr_dropped=stderr_buffer.dropped,
            timed_out=timed_out,
            elapsed_ms=elapsed_ms
        )

    @classmethod
    async def _start_shell(
        cls,
        command: str,
        *,
        shell: list[str] | None,
        cwd: str | None,
        env: dict[str, str] | None
    ) -> asyncio.subprocess.Process:
        """按指定 shell 入口启动命令。"""
        prefix = [str(item) for item in (shell or []) if str(item or "").strip()]

        kwargs = {
            "cwd": cwd or None,
            "env": env,
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            **cls.subprocess_process_group_kwargs()
        }

        if prefix:
            return await asyncio.create_subprocess_exec(*prefix, command, **kwargs)

        return await asyncio.create_subprocess_shell(command, **kwargs)

    @classmethod
    async def _read_stream(
        cls,
        stream: asyncio.StreamReader | None,
        buffer: _CaptureBuffer,
        output_buffer: _OrderedOutputBuffer,
        stream_name: str
    ) -> None:
        """持续读取输出流。"""
        if stream is None:
            return None

        while True:
            try:
                chunk = await stream.read(cls.CHUNK_BYTES)
            except (OSError, RuntimeError, ValueError):
                return None
            if not chunk:
                return None
            buffer.append(chunk)
            await output_buffer.append(stream_name, chunk)

    @classmethod
    async def _drain_readers(
        cls,
        process: asyncio.subprocess.Process,
        tasks: list[asyncio.Task[None]]
    ) -> None:
        """短暂收束输出读取任务。"""
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=cls.IO_DRAIN_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        cls.close_process_transport(process)

    @classmethod
    async def terminate_process_tree(
        cls,
        process: asyncio.subprocess.Process,
        *,
        force: bool
    ) -> None:
        """终止进程组或进程树。"""
        if process.returncode is not None:
            return None

        if os.name == "nt":
            await cls._terminate_windows_process_tree(process, force=force)
            return None

        signal_number = signal.SIGKILL if force else signal.SIGTERM

        if not cls._signal_posix_process_group(process, signal_number):
            cls._signal_top_process(process, force=force)

        if not force:
            await cls.wait_for_process(process, int(cls.TERMINATE_GRACE_SEC * 1000))
            if process.returncode is None:
                if not cls._signal_posix_process_group(process, signal.SIGKILL):
                    cls._signal_top_process(process, force=True)

        await cls.wait_for_process(process, 1000)

    @classmethod
    async def _terminate_windows_process_tree(
        cls,
        process: asyncio.subprocess.Process,
        *,
        force: bool
    ) -> None:
        """在 Windows 上终止进程树。"""
        if not force:
            await cls.send_windows_ctrl_break(process)
            await cls.wait_for_process(process, int(cls.TERMINATE_GRACE_SEC * 1000))
            if process.returncode is not None:
                return None

        killed = await cls.taskkill_process_tree(process.pid, force=True)
        if not killed:
            cls._signal_top_process(process, force=True)
        await cls.wait_for_process(process, 1000)

    @staticmethod
    def subprocess_process_group_kwargs() -> dict[str, typing.Any]:
        """返回建立进程组或会话所需的启动参数。"""
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            return {"creationflags": flags} if flags else {}
        return {"start_new_session": True}

    @staticmethod
    async def wait_for_process(
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
    async def send_windows_ctrl_break(
        process: asyncio.subprocess.Process
    ) -> None:
        """向 Windows 新进程组发送 CTRL_BREAK。"""
        break_signal = getattr(signal, "CTRL_BREAK_EVENT", None)
        if break_signal is None:
            return None
        try:
            process.send_signal(break_signal)
        except (ProcessLookupError, RuntimeError, ValueError, OSError):
            return None

    @staticmethod
    async def taskkill_process_tree(
        pid: int,
        *,
        force: bool
    ) -> bool:
        """调用 taskkill 终止 Windows 进程树。"""
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.append("/F")

        task: asyncio.subprocess.Process | None = None
        try:
            task = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(task.wait(), timeout=2)
        except (OSError, RuntimeError, ValueError):
            return False
        except asyncio.TimeoutError:
            if task is not None and task.returncode is None:
                try:
                    task.kill()
                except (ProcessLookupError, RuntimeError, ValueError):
                    pass
                await asyncio.gather(task.wait(), return_exceptions=True)
            return False

        return task.returncode == 0

    @staticmethod
    def close_process_transport(process: asyncio.subprocess.Process) -> None:
        """关闭 asyncio 进程传输对象。"""
        transport = getattr(process, "_transport", None)

        close = getattr(transport, "close", None)
        if not callable(close):
            return None

        try:
            close()
        except (RuntimeError, ValueError, OSError):
            return None

    @staticmethod
    def _signal_posix_process_group(
        process: asyncio.subprocess.Process,
        signal_number: int
    ) -> bool:
        """向 POSIX 进程组发送信号。"""
        try:
            os.killpg(process.pid, signal_number)
            return True
        except (ProcessLookupError, PermissionError, RuntimeError, ValueError, OSError):
            return False

    @staticmethod
    def _signal_top_process(
        process: asyncio.subprocess.Process,
        *,
        force: bool
    ) -> None:
        """向顶层进程发送兜底终止信号。"""
        try:
            if force:
                process.kill()
            else:
                process.terminate()
        except (ProcessLookupError, RuntimeError, ValueError):
            return None


if __name__ == '__main__':
    pass
