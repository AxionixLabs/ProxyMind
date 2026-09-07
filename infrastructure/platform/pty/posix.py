# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import ctypes
import ctypes.util
import errno
import os
import signal
import sys
import typing
from pathlib import Path

from agent.ports.interactive_process import TerminalSize
from infrastructure.platform.pty.contract import PtyEndOfFile


_MACOS_PROCESS_BATCH_SIZE = 16


def _macos_process_group_members(process_group_id: int) -> tuple[int, ...]:
    """列出 macOS 指定进程组当前仍存在的成员。"""
    library_name = ctypes.util.find_library("proc")
    if library_name is None:
        raise OSError("macOS libproc is unavailable")
    library = ctypes.CDLL(library_name, use_errno=True)
    list_processes = library.proc_listpgrppids
    list_processes.argtypes = (
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_int,
    )
    list_processes.restype = ctypes.c_int

    capacity = _MACOS_PROCESS_BATCH_SIZE
    while True:
        process_ids = (ctypes.c_int * capacity)()
        count = list_processes(
            process_group_id,
            process_ids,
            ctypes.sizeof(process_ids),
        )
        if count < 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))
        if count < capacity:
            return tuple(
                process_id
                for process_id in process_ids[:count]
                if process_id > 0
            )
        capacity *= 2


def _signal_macos_process_group_members(
    process_group_id: int,
    value: signal.Signals,
) -> None:
    """在 macOS 拒绝组信号时逐个通知仍属于目标组的成员。"""
    process_ids = sorted(
        _macos_process_group_members(process_group_id),
        key=lambda process_id: process_id == process_group_id,
    )
    delivered = False
    first_error: OSError | None = None
    for process_id in process_ids:
        try:
            if os.getpgid(process_id) != process_group_id:
                continue
            os.kill(process_id, value)
            delivered = True
        except ProcessLookupError:
            continue
        except OSError as error:
            if first_error is None:
                first_error = error
    if not delivered and first_error is not None:
        raise first_error


class PosixPtyBackend:
    """通过 pexpect 拥有 POSIX controlling TTY 与子进程组。"""

    def __init__(
        self,
        argv: typing.Sequence[str],
        *,
        cwd: Path,
        env: typing.Mapping[str, str],
        size: TerminalSize,
    ) -> None:
        try:
            import pexpect
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "POSIX PTY requires the pexpect package"
            ) from error
        self._child = pexpect.spawn(
            argv[0],
            list(argv[1:]),
            cwd=str(cwd),
            env=dict(env),
            encoding=None,
            echo=False,
            timeout=None,
            dimensions=(size.rows, size.columns),
        )
        self._input_closed = False
        self._closed = False

    @property
    def pid(self) -> int:
        """返回 PTY session leader 的进程标识。"""
        return self._child.pid

    def read(self, size: int) -> bytes:
        """读取原生 PTY 字节。"""
        try:
            chunk = os.read(self._child.fileno(), size)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            raise PtyEndOfFile from exc
        if not chunk:
            raise PtyEndOfFile
        return chunk

    def write(self, data: bytes) -> int:
        """循环推进原生 PTY 写入，避免短写丢失输入。"""
        if self._input_closed:
            raise RuntimeError("POSIX PTY input is closed")
        total = 0
        while total < len(data):
            written = self._child.send(data[total:])
            if written == 0:
                raise OSError("POSIX PTY input write made no progress")
            total += written
        return total

    def close_input(self) -> None:
        """通过 controlling TTY 的 VEOF 字符交付输入结束。"""
        if self._input_closed:
            return
        self._child.send(b"\x04")
        self._input_closed = True

    def resize(self, size: TerminalSize) -> None:
        """更新 PTY 窗口行列。"""
        self._child.setwinsize(size.rows, size.columns)

    def interrupt(self) -> None:
        """向 controlling TTY 写入 Ctrl-C。"""
        self._child.send(b"\x03")

    def is_alive(self) -> bool:
        """判断根子进程是否存活。"""
        return self._child.isalive()

    def wait(self) -> int:
        """等待并返回 POSIX 退出码。"""
        status = self._child.wait()
        self._signal_group(signal.SIGTERM)
        if status is not None:
            return int(status)
        signal_status = self._child.signalstatus
        if signal_status is not None:
            return -int(signal_status)
        raise RuntimeError("PTY child exited without status")

    def terminate(self) -> None:
        """终止 PTY 进程组。"""
        self._signal_group(signal.SIGTERM)

    def kill(self) -> None:
        """强制终止 PTY 进程组。"""
        self._signal_group(signal.SIGKILL)

    def close(self) -> None:
        """释放 pexpect 文件描述符。"""
        if self._closed:
            return
        self._child.close(force=False)
        self._closed = True

    def _signal_group(self, value: signal.Signals) -> None:
        """只向本 adapter 创建的 session 进程组发信号。"""
        try:
            os.killpg(self._child.pid, value)
        except ProcessLookupError:
            return
        except PermissionError:
            if sys.platform != "darwin":
                raise
            _signal_macos_process_group_members(self._child.pid, value)


if __name__ == '__main__':
    pass
