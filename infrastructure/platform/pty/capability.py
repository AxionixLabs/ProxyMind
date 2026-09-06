# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

from agent.ports import CapabilityError
from agent.ports.interactive_process import InteractiveProcessHandle
from agent.ports.interactive_process import InteractiveProcessSpec
from agent.ports.interactive_process import TerminalSize
from infrastructure.platform.pty.contract import NativePtyBackend
from infrastructure.platform.pty.contract import PtyEndOfFile
from infrastructure.platform.pty.factory import spawn_native_pty

__all__ = ("LocalInteractiveProcessCapability",)


class _NativeInteractiveProcessHandle:
    """把同步原生 PTY 收口为可取消的异步进程句柄。"""

    OUTPUT_CHUNK_BYTES = 65536
    EXIT_POLL_SEC = 0.02
    WINDOWS_OUTPUT_QUIET_SEC = 0.2

    def __init__(self, backend: NativePtyBackend) -> None:
        """绑定原生 PTY 并初始化串行化生命周期状态。"""
        self._backend = backend
        self.session_id = f"interactive_process_{secrets.token_hex(8)}"
        self.pid: int | None = backend.pid
        self.returncode: int | None = None
        self._input_closed = False
        self._closed = False
        self._reader_claimed = False
        self._output_revision = 0
        self._output_changed = asyncio.Event()
        self._operation_lock = asyncio.Lock()
        self._wait_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()

    async def read_output(self) -> AsyncIterator[bytes]:
        """在受控工作线程中持续读取合并后的原始终端输出。"""
        if self._reader_claimed:
            raise CapabilityError(
                "interactive_process_output_claimed",
                "interactive process output already has a consumer",
            )
        self._reader_claimed = True
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(
                        self._backend.read,
                        self.OUTPUT_CHUNK_BYTES,
                    )
                except PtyEndOfFile:
                    return
                except OSError as error:
                    if self._closed:
                        return
                    raise CapabilityError(
                        "interactive_process_read_failed",
                        str(error).strip() or "interactive process output failed",
                        retryable=True,
                        details={"exception_type": type(error).__name__},
                    ) from error
                if not chunk:
                    continue
                self._output_revision += 1
                self._output_changed.set()
                yield bytes(chunk)
        finally:
            self._output_changed.set()

    async def write(self, data: str, *, eof: bool = False) -> None:
        """串行写入 UTF-8 文本，并可选交付终端 EOF。"""
        self._require_open()
        if not isinstance(data, str):
            raise CapabilityError(
                "interactive_process_input_invalid",
                "interactive process input must be a string",
            )
        async with self._operation_lock:
            self._require_open()
            if self._input_closed:
                raise CapabilityError(
                    "interactive_process_stdin_closed",
                    "interactive process input is closed",
                )
            try:
                payload = data.encode("utf-8")
                if payload:
                    written = await asyncio.to_thread(self._backend.write, payload)
                    if written != len(payload):
                        raise OSError(
                            "interactive process input short write: "
                            f"expected {len(payload)}, wrote {written}"
                        )
                if eof:
                    await asyncio.to_thread(self._backend.close_input)
                    self._input_closed = True
            except (OSError, RuntimeError) as error:
                raise CapabilityError(
                    "interactive_process_write_failed",
                    str(error).strip() or "interactive process input failed",
                    retryable=True,
                    details={"exception_type": type(error).__name__},
                ) from error

    async def interrupt(self) -> None:
        """串行向原生终端发送 Ctrl-C。"""
        self._require_open()
        async with self._operation_lock:
            self._require_open()
            if self._input_closed:
                raise CapabilityError(
                    "interactive_process_stdin_closed",
                    "interactive process input is closed",
                )
            try:
                await asyncio.to_thread(self._backend.interrupt)
            except (OSError, RuntimeError) as error:
                raise CapabilityError(
                    "interactive_process_interrupt_failed",
                    str(error).strip() or "interactive process interrupt failed",
                    details={"exception_type": type(error).__name__},
                ) from error

    async def resize(self, size: TerminalSize) -> None:
        """串行调整原生终端尺寸。"""
        self._require_open()
        if not isinstance(size, TerminalSize):
            raise TypeError("interactive process size must be TerminalSize")
        async with self._operation_lock:
            self._require_open()
            try:
                await asyncio.to_thread(self._backend.resize, size)
            except (OSError, RuntimeError) as error:
                raise CapabilityError(
                    "interactive_process_resize_failed",
                    str(error).strip() or "interactive process resize failed",
                    details={"exception_type": type(error).__name__},
                ) from error

    async def wait(self) -> int:
        """等待根进程退出，保留尾部输出后关闭原生终端。"""
        self._require_open()
        async with self._wait_lock:
            if self.returncode is not None:
                return self.returncode
            while await asyncio.to_thread(self._backend.is_alive):
                await asyncio.sleep(self.EXIT_POLL_SEC)
            if os.name == "nt":
                await self._wait_for_output_quiet()
            try:
                self.returncode = int(await asyncio.to_thread(self._backend.wait))
            except (OSError, RuntimeError) as error:
                raise CapabilityError(
                    "interactive_process_wait_failed",
                    str(error).strip() or "interactive process wait failed",
                    details={"exception_type": type(error).__name__},
                ) from error
            return self.returncode

    async def terminate(self, *, force: bool = False) -> None:
        """请求原生 adapter 终止完整进程树。"""
        self._require_open()
        async with self._operation_lock:
            self._require_open()
            if self.returncode is not None:
                return
            try:
                if await asyncio.to_thread(self._backend.is_alive):
                    operation = self._backend.kill if force else self._backend.terminate
                    await asyncio.to_thread(operation)
            except (OSError, RuntimeError) as error:
                raise CapabilityError(
                    "interactive_process_terminate_failed",
                    str(error).strip() or "interactive process termination failed",
                    details={"exception_type": type(error).__name__},
                ) from error

    async def aclose(self) -> None:
        """幂等强制收束进程树并释放原生句柄。"""
        async with self._close_lock:
            if self._closed:
                return
            try:
                if self.returncode is None:
                    await self.terminate(force=True)
                    await self.wait()
            finally:
                self._closed = True
                await asyncio.to_thread(self._backend.close)

    async def _wait_for_output_quiet(self) -> None:
        """等待 ConPTY 在根进程退出后交付剩余异步输出。"""
        loop = asyncio.get_running_loop()
        quiet_deadline = loop.time() + self.WINDOWS_OUTPUT_QUIET_SEC
        revision = self._output_revision
        while True:
            remaining = quiet_deadline - loop.time()
            if remaining <= 0:
                return
            self._output_changed.clear()
            if self._output_revision != revision:
                revision = self._output_revision
                quiet_deadline = loop.time() + self.WINDOWS_OUTPUT_QUIET_SEC
                continue
            try:
                await asyncio.wait_for(
                    self._output_changed.wait(),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                return

    def _require_open(self) -> None:
        """拒绝关闭后的交互式进程操作。"""
        if self._closed:
            raise CapabilityError(
                "interactive_process_closed",
                "interactive process handle is closed",
            )


class LocalInteractiveProcessCapability:
    """在当前平台创建并拥有原生交互式 PTY 进程。"""

    def __init__(self) -> None:
        """初始化进程句柄集合与关闭互斥。"""
        self._handles: dict[str, InteractiveProcessHandle] = {}
        self._closed = False
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        spec: InteractiveProcessSpec,
    ) -> InteractiveProcessHandle:
        """创建原生 PTY，并在失败时返回稳定能力错误。"""
        if not isinstance(spec, InteractiveProcessSpec):
            raise TypeError("interactive process spec must be InteractiveProcessSpec")
        async with self._lock:
            if self._closed:
                raise CapabilityError(
                    "interactive_process_capability_closed",
                    "interactive process capability is closed",
                )
            cwd = Path(spec.cwd).expanduser().resolve()
            if not cwd.is_dir():
                raise CapabilityError(
                    "interactive_process_cwd_invalid",
                    f"interactive process cwd is not a directory: {cwd}",
                )
            try:
                backend = await asyncio.to_thread(
                    spawn_native_pty,
                    spec.argv,
                    cwd=cwd,
                    env={**os.environ, **dict(spec.env)},
                    size=spec.size,
                )
            except (ImportError, OSError, RuntimeError, ValueError) as error:
                raise CapabilityError(
                    "interactive_process_spawn_failed",
                    str(error).strip() or "interactive process spawn failed",
                    retryable=True,
                    details={"exception_type": type(error).__name__},
                ) from error
            handle = _NativeInteractiveProcessHandle(backend)
            self._handles[handle.session_id] = handle
            return handle

    async def aclose(self) -> None:
        """幂等关闭全部由本能力创建的 PTY 进程。"""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            handles = tuple(self._handles.values())
            self._handles.clear()
        await asyncio.gather(
            *(handle.aclose() for handle in handles),
            return_exceptions=True,
        )


if __name__ == '__main__':
    pass
