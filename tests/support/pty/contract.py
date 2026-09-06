import os
import threading
import time
import typing
import types
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class PtyEndOfFile(Exception):
    """表示伪终端输出已经完整关闭。"""


class PtyBackend(typing.Protocol):
    """定义单个原生 PTY 子进程的同步平台边界。

    实现方拥有原生终端和子进程树，必须让读取可由 `close` 解除阻塞，并保证
    `terminate`、`kill` 与 `close` 可重复调用。
    """

    @property
    def pid(self) -> int:
        """返回根子进程标识。"""

    def read(self, size: int) -> bytes:
        """阻塞读取至多指定字节，终端关闭时抛出 `PtyEndOfFile`。"""

    def write(self, data: bytes) -> int:
        """向终端写入完整输入字节。"""

    def close_input(self) -> None:
        """向子进程交付终端 EOF，并拒绝后续输入。"""

    def resize(self, size: "TerminalSize") -> None:
        """修改原生终端窗口尺寸。"""

    def interrupt(self) -> None:
        """通过终端发送 Ctrl-C。"""

    def is_alive(self) -> bool:
        """判断根子进程是否仍在运行。"""

    def wait(self) -> int:
        """等待根子进程并返回退出码。"""

    def terminate(self) -> None:
        """终止所拥有的进程树。"""

    def kill(self) -> None:
        """强制终止所拥有的进程树。"""

    def close(self) -> None:
        """释放终端、进程和平台句柄。"""


@dataclass(frozen=True, slots=True)
class TerminalSize:
    """描述 PTY 的字符行列尺寸。"""

    rows: int = 24
    columns: int = 80

    def __post_init__(self) -> None:
        if self.rows <= 0 or self.columns <= 0:
            raise ValueError("terminal rows and columns must be positive")


class PtyKey(Enum):
    """定义验收场景使用的稳定终端按键字节。"""

    ENTER = b"\r"
    TAB = b"\x09"
    CTRL_C = b"\x03"
    ESCAPE = b"\x1b"
    BACKSPACE = b"\x7f"
    CTRL_D = b"\x04"


class PtySession:
    """拥有单个原生 PTY、持续输出 reader 和完整关闭生命周期。"""

    def __init__(self, backend: PtyBackend) -> None:
        self._backend = backend
        self._condition = threading.Condition()
        self._output = bytearray()
        self._output_closed = False
        self._reader_error: Exception | None = None
        self._input_closed = False
        self._closed = False
        self._lifecycle_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._reader = threading.Thread(
            target=self._read_output,
            name=f"pty-reader-{backend.pid}",
            daemon=True,
        )
        self._reader.start()

    @property
    def pid(self) -> int:
        """返回根子进程标识。"""
        return self._backend.pid

    @property
    def output_closed(self) -> bool:
        """判断原生 PTY 输出是否已经关闭。"""
        with self._condition:
            return self._output_closed

    def output(self) -> bytes:
        """返回当前已收集输出的不可变快照。"""
        with self._condition:
            return bytes(self._output)

    def output_text(self) -> str:
        """以替换非法序列的方式返回当前 UTF-8 输出。"""
        return self.output().decode("utf-8", errors="replace")

    def wait_for_output_change(
        self,
        offset: int,
        timeout: float,
    ) -> tuple[bytes, bool]:
        """等待指定偏移后出现新输出或 PTY 关闭。"""
        if offset < 0:
            raise ValueError("PTY output offset must not be negative")
        deadline = time.monotonic() + timeout
        with self._condition:
            if offset > len(self._output):
                raise ValueError("PTY output offset exceeds collected output")
            while len(self._output) == offset and not self._output_closed:
                self._raise_reader_error()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for PTY output change")
                self._condition.wait(remaining)
            self._raise_reader_error()
            return bytes(self._output), self._output_closed

    def write(self, data: bytes) -> None:
        """完整写入终端输入。"""
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("PTY session is closed")
            if self._input_closed:
                raise RuntimeError("PTY input is closed")
            with self._write_lock:
                written = self._backend.write(data)
            if written != len(data):
                raise OSError(
                    f"PTY input short write: expected {len(data)}, wrote {written}"
                )

    def write_text(self, text: str) -> None:
        """以 UTF-8 写入用户文本。"""
        self.write(text.encode("utf-8"))

    def send_key(self, key: PtyKey) -> None:
        """写入稳定按键字节。"""
        if key is PtyKey.CTRL_C:
            with self._lifecycle_lock:
                if self._closed:
                    raise RuntimeError("PTY session is closed")
                if self._input_closed:
                    raise RuntimeError("PTY input is closed")
                with self._write_lock:
                    self._backend.interrupt()
            return
        self.write(key.value)

    def close_input(self) -> None:
        """幂等交付终端 EOF，并阻止后续写入。"""
        with self._lifecycle_lock:
            if self._closed or self._input_closed:
                return
            with self._write_lock:
                self._backend.close_input()
            self._input_closed = True

    def resize(self, size: TerminalSize) -> None:
        """调整原生终端尺寸。"""
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("PTY session is closed")
            self._backend.resize(size)

    def wait_for_output(self, expected: str, timeout: float = 5.0) -> bytes:
        """等待输出包含目标文本，并返回命中时的字节快照。"""
        expected_bytes = expected.encode("utf-8")
        deadline = time.monotonic() + timeout
        with self._condition:
            while expected_bytes not in self._output:
                self._raise_reader_error()
                if self._output_closed:
                    raise AssertionError(
                        f"PTY closed before output appeared: {expected!r}; "
                        f"output={self.output_text()!r}"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"timed out waiting for PTY output: {expected!r}; "
                        f"output={self.output_text()!r}"
                    )
                self._condition.wait(remaining)
            return bytes(self._output)

    def wait_for_exit(self, timeout: float = 5.0) -> int:
        """在截止时间内等待根进程退出并排空 PTY 输出。"""
        deadline = time.monotonic() + timeout
        while self._backend.is_alive():
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for PTY process {self.pid} to exit"
                )
            with self._condition:
                self._condition.wait(min(0.05, deadline - time.monotonic()))

        exit_code = self._backend.wait()
        with self._condition:
            while not self._output_closed:
                self._raise_reader_error()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"timed out draining PTY output for process {self.pid}"
                    )
                self._condition.wait(remaining)
            self._raise_reader_error()
        return exit_code

    def terminate(self, timeout: float = 2.0) -> int:
        """终止进程树，超时后升级为强制终止。"""
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("PTY session is closed")
            if self._backend.is_alive():
                self._backend.terminate()
            try:
                return self.wait_for_exit(timeout)
            except TimeoutError:
                self._backend.kill()
                return self.wait_for_exit(timeout)

    def kill(self, timeout: float = 2.0) -> int:
        """强制终止进程树并等待输出关闭。"""
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("PTY session is closed")
            self._backend.kill()
            return self.wait_for_exit(timeout)

    def close(self) -> None:
        """幂等终止残留进程并释放 reader 与原生句柄。"""
        with self._lifecycle_lock:
            if self._closed:
                return
            try:
                if self._backend.is_alive():
                    self.terminate()
            finally:
                self._backend.close()
                self._reader.join(timeout=2.0)
                self._input_closed = True
                self._closed = True
        if self._reader.is_alive():
            raise TimeoutError(f"PTY reader for process {self.pid} did not stop")
        with self._condition:
            self._raise_reader_error()

    def __enter__(self) -> "PtySession":
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        self.close()

    def _read_output(self) -> None:
        """持续排空原生终端输出并唤醒等待方。"""
        try:
            while True:
                chunk = self._backend.read(65536)
                if not chunk:
                    continue
                with self._condition:
                    self._output.extend(chunk)
                    self._condition.notify_all()
        except PtyEndOfFile:
            pass
        except Exception as exc:
            with self._condition:
                self._reader_error = exc
        finally:
            with self._condition:
                self._output_closed = True
                self._condition.notify_all()

    def _raise_reader_error(self) -> None:
        """在调用线程传播 reader 的真实失败。"""
        error = self._reader_error
        if error is not None:
            raise RuntimeError("PTY output reader failed") from error


def spawn_pty(
    argv: typing.Sequence[str],
    *,
    cwd: Path,
    env: typing.Mapping[str, str],
    size: TerminalSize = TerminalSize(),
) -> PtySession:
    """使用当前平台原生实现创建并拥有 PTY 会话。"""
    if not argv:
        raise ValueError("PTY argv must not be empty")
    if os.name == "nt":
        from .windows import WindowsPtyBackend

        backend: PtyBackend = WindowsPtyBackend(argv, cwd=cwd, env=env, size=size)
    else:
        from .posix import PosixPtyBackend

        backend = PosixPtyBackend(argv, cwd=cwd, env=env, size=size)
    return PtySession(backend)
