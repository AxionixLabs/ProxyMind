# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import sys
import typing

import anyio

from collections.abc import AsyncIterator
from pathlib import Path

from anyio.abc import (
    ByteReceiveStream,
    ByteSendStream,
    Process,
)
from mcp.os.posix.utilities import terminate_posix_process_tree
from mcp.os.win32.utilities import (
    FallbackProcess,
    create_windows_process,
    get_windows_executable_command,
    terminate_windows_process_tree,
)


@contextlib.asynccontextmanager
async def open_stdio_process(
    command: str, args: list[str], *, env: dict[str, str],
    cwd: str | Path | None, stderr: typing.TextIO,
) -> AsyncIterator[tuple[ByteReceiveStream, ByteSendStream]]:
    """持有协议子进程及管道；调用方须在同一任务退出，关闭时限与进程树操作由本适配器负责。"""
    if sys.platform == "win32":
        process = await create_windows_process(
            get_windows_executable_command(command), args, env, stderr, cwd,
        )
    else:
        process = await anyio.open_process(
            [command, *args], env=env, cwd=cwd, stderr=stderr, start_new_session=True,
        )
    try:
        stdout, stdin = process.stdout, process.stdin
        if stdout is None or stdin is None:
            raise RuntimeError("MCP subprocess requires stdin and stdout pipes")
        yield stdout, stdin
    finally:
        with anyio.CancelScope(shield=True):
            try:
                if process.stdin is not None:
                    with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
                        await process.stdin.aclose()
                try:
                    with anyio.fail_after(2.0):
                        if isinstance(process, FallbackProcess):
                            await anyio.to_thread.run_sync(process.popen.wait, abandon_on_cancel=True)
                        else:
                            await process.wait()
                except TimeoutError:
                    if sys.platform == "win32":
                        await terminate_windows_process_tree(process)
                    elif isinstance(process, Process):
                        await terminate_posix_process_tree(process)
            finally:
                await process.__aexit__(None, None, None)


if __name__ == '__main__':
    pass
