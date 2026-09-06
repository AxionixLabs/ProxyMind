import os
import signal
import typing
from pathlib import Path

import pexpect

from .contract import PtyEndOfFile
from .contract import TerminalSize


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
        self._closed = False

    @property
    def pid(self) -> int:
        """返回 PTY session leader 的进程标识。"""
        return self._child.pid

    def read(self, size: int) -> bytes:
        """读取原生 PTY 字节。"""
        try:
            return self._child.read_nonblocking(size, timeout=None)
        except pexpect.EOF as exc:
            raise PtyEndOfFile from exc

    def write(self, data: bytes) -> int:
        """写入原生 PTY 字节。"""
        return self._child.send(data)

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
        if not self._child.isalive():
            return
        try:
            os.killpg(self._child.pid, value)
        except ProcessLookupError:
            return
