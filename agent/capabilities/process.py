# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import os
import secrets
import typing
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
)

from agent.ports import (
    CapabilityError,
    ProcessCapability,
    ProcessHandle,
    ProcessSpec,
)
from metadata import const

SandboxProcessLauncher: typing.TypeAlias = Callable[
    [ProcessSpec],
    Awaitable[ProcessHandle],
]


class _SubprocessHandle:
    """把 asyncio 子进程收口为进程能力句柄。"""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        """绑定子进程并生成本地会话身份。"""
        self._process = process
        self.session_id: str = f"process_{secrets.token_hex(8)}"
        self.pid: int | None = process.pid
        self.returncode: int | None = process.returncode
        self._closed = False

    async def read_stdout(self) -> AsyncIterator[str]:
        """按块读取标准输出并以 UTF-8 替换错误解码。"""
        self._require_open()
        async for chunk in _read_stream(self._process.stdout):
            yield chunk

    async def read_stderr(self) -> AsyncIterator[str]:
        """按块读取标准错误并以 UTF-8 替换错误解码。"""
        self._require_open()
        async for chunk in _read_stream(self._process.stderr):
            yield chunk

    async def write(self, data: str, *, eof: bool = False) -> None:
        """向标准输入写入文本并可选发送 EOF。"""
        self._require_open()
        if self._process.stdin is None:
            raise CapabilityError(
                "process_stdin_unavailable",
                "process stdin is not available",
            )
        if not isinstance(data, str):
            raise CapabilityError(
                "process_input_invalid",
                "process input must be a string",
            )
        try:
            if data:
                self._process.stdin.write(data.encode("utf-8"))
                await self._process.stdin.drain()
            if eof:
                self._process.stdin.close()
                await self._process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionError, OSError) as error:
            raise CapabilityError(
                "process_write_failed",
                str(error).strip() or "process input failed",
                retryable=True,
                details={"exception_type": type(error).__name__},
            ) from error

    async def wait(self) -> int:
        """等待子进程退出并保存退出码。"""
        self._require_open()
        self.returncode = await self._process.wait()
        return int(self.returncode)

    async def terminate(self, *, force: bool = False) -> None:
        """请求子进程优雅退出或强制终止。"""
        self._require_open()
        if self._process.returncode is not None:
            self.returncode = self._process.returncode
            return None
        if force:
            self._process.kill()
        else:
            self._process.terminate()

    async def aclose(self) -> None:
        """幂等终止并等待子进程回收。"""
        if self._closed:
            return None
        try:
            await self.terminate(force=True)
            await self.wait()
        finally:
            self._closed = True

    def _require_open(self) -> None:
        """拒绝在关闭后继续访问进程句柄。"""
        if self._closed:
            raise CapabilityError("process_closed", "process handle is closed")


class LocalProcessCapability:
    """提供标准库实现的本地受控进程能力。"""

    def __init__(
        self,
        *,
        sandbox_launcher: SandboxProcessLauncher | None = None,
    ) -> None:
        """绑定可选的本地 sandbox 启动器。"""
        self._sandbox_launcher = sandbox_launcher
        self._handles: dict[str, ProcessHandle] = {}
        self._closed: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()

    async def spawn(self, spec: ProcessSpec) -> ProcessHandle:
        """按启动参数创建进程，并登记句柄所有权。"""
        if not isinstance(spec, ProcessSpec):
            raise TypeError("process spec must be ProcessSpec")
        handle: ProcessHandle
        async with self._lock:
            if self._closed:
                raise CapabilityError("process_closed", "process capability is closed")
            if spec.sandbox_mode != "danger-full-access":
                launcher = self._sandbox_launcher
                if launcher is None:
                    raise CapabilityError(
                        "process_sandbox_unavailable",
                        "restricted process requires a sandbox launcher",
                    )
                try:
                    handle = await launcher(spec)
                except CapabilityError:
                    raise
                except Exception as error:
                    raise CapabilityError(
                        "process_spawn_failed",
                        str(error).strip() or "sandbox process spawn failed",
                        retryable=True,
                        details={"exception_type": type(error).__name__},
                    ) from error
            else:
                try:
                    process = await asyncio.create_subprocess_exec(
                        *spec.argv,
                        cwd=spec.cwd,
                        env={**os.environ, **dict(spec.env)},
                        stdin=(
                            asyncio.subprocess.PIPE
                            if spec.stdin_open
                            else asyncio.subprocess.DEVNULL
                        ),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                except OSError as error:
                    raise CapabilityError(
                        "process_spawn_failed",
                        str(error).strip() or "process spawn failed",
                        retryable=True,
                        details={"exception_type": type(error).__name__},
                    ) from error
                handle = _SubprocessHandle(process)

            self._handles[handle.session_id] = handle
            return handle

    async def aclose(self) -> None:
        """关闭能力并回收全部已创建进程。"""
        async with self._lock:
            if self._closed:
                return None
            self._closed = True
            handles = tuple(self._handles.values())
            self._handles.clear()
        await asyncio.gather(
            *(handle.aclose() for handle in handles),
            return_exceptions=True,
        )


class InMemoryProcessHandle:
    """提供可断言输入输出和终止语义的内存进程句柄。"""

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> None:
        """绑定固定输出和退出码。"""
        self._stdout = (str(stdout),) if stdout else ()
        self._stderr = (str(stderr),) if stderr else ()
        self._inputs: list[str] = []
        self._stdin_closed: bool = False
        self._closed: bool = False
        self.session_id: str = f"memory_process_{secrets.token_hex(8)}"
        self.pid: int | None = None
        self.returncode: int | None = int(returncode)

    @property
    def inputs(self) -> tuple[str, ...]:
        """返回已写入的标准输入快照。"""
        return tuple(self._inputs)

    @property
    def stdin_closed(self) -> bool:
        """返回是否已收到标准输入 EOF。"""
        return self._stdin_closed

    async def read_stdout(self) -> AsyncIterator[str]:
        """读取固定标准输出。"""
        self._require_open()
        for chunk in self._stdout:
            yield chunk

    async def read_stderr(self) -> AsyncIterator[str]:
        """读取固定标准错误。"""
        self._require_open()
        for chunk in self._stderr:
            yield chunk

    async def write(self, data: str, *, eof: bool = False) -> None:
        """记录标准输入并可选关闭输入。"""
        self._require_open()
        if not isinstance(data, str):
            raise CapabilityError("process_input_invalid", "process input must be a string")
        if self._stdin_closed:
            raise CapabilityError("process_stdin_closed", "process stdin is closed")
        if data:
            self._inputs.append(data)
        if eof:
            self._stdin_closed = True

    async def wait(self) -> int:
        """返回固定退出码。"""
        self._require_open()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    async def terminate(self, *, force: bool = False) -> None:
        """以稳定的信号式退出码收束内存进程。"""
        self._require_open()
        if self.returncode is None:
            self.returncode = -9 if force else -15

    async def aclose(self) -> None:
        """幂等关闭内存进程句柄。"""
        self._closed = True

    def _require_open(self) -> None:
        """拒绝在关闭后继续访问内存进程。"""
        if self._closed:
            raise CapabilityError("process_closed", "process handle is closed")


class InMemoryProcessCapability:
    """提供无子进程副作用的进程能力替身。"""

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
    ) -> None:
        """绑定每个新句柄使用的固定输出和退出码。"""
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = int(returncode)
        self._handles: dict[str, InMemoryProcessHandle] = {}
        self._closed: bool = False

    async def spawn(self, spec: ProcessSpec) -> ProcessHandle:
        """登记启动规格并返回内存进程句柄。"""
        if not isinstance(spec, ProcessSpec):
            raise TypeError("process spec must be ProcessSpec")
        if self._closed:
            raise CapabilityError("process_closed", "process capability is closed")
        handle = InMemoryProcessHandle(
            stdout=self._stdout,
            stderr=self._stderr,
            returncode=self._returncode,
        )
        self._handles[handle.session_id] = handle
        return handle

    async def aclose(self) -> None:
        """关闭能力并回收所有内存句柄。"""
        if self._closed:
            return None
        self._closed = True
        handles = tuple(self._handles.values())
        self._handles.clear()
        for handle in handles:
            await handle.aclose()


async def _read_stream(
    stream: asyncio.StreamReader | None,
) -> AsyncIterator[str]:
    """读取可选进程流并将字节解码为文本。"""
    if stream is None:
        return
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        yield chunk.decode(const.CHARSET, errors="replace")


if not isinstance(LocalProcessCapability(), ProcessCapability):
    raise TypeError("LocalProcessCapability must implement ProcessCapability")
if not isinstance(InMemoryProcessCapability(), ProcessCapability):
    raise TypeError("InMemoryProcessCapability must implement ProcessCapability")

if __name__ == '__main__':
    pass
