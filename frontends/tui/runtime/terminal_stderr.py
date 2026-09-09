# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import types
import typing


class TerminalStderrGuard(object):
    """在交互 TUI 生命周期内将进程级 stderr 临时指向空设备。"""

    def __init__(self) -> None:
        """初始化尚未接管 stderr 的 guard。"""
        self._active = False
        self._saved_fd: int | None = None
        self._devnull: typing.IO[str] | None = None

    @classmethod
    def install(cls) -> "TerminalStderrGuard":
        """在 stdout/stderr 共用交互终端时安装 stderr 隔离。"""
        guard = cls()
        if not _stderr_targets_terminal():
            return guard

        try:
            guard._saved_fd = os.dup(2)
            guard._devnull = open(
                os.devnull,
                mode="w",
                encoding="utf-8",
                errors="ignore",
            )
            os.dup2(guard._devnull.fileno(), 2)
            _sync_windows_standard_error_handle()
            guard._active = True
        except OSError:
            try:
                guard.close()
            except OSError:
                pass
        return guard

    def close(self) -> None:
        """恢复安装前的 stderr，并释放空设备句柄。"""
        saved_fd = self._saved_fd
        restore_error: OSError | None = None
        if saved_fd is not None:
            try:
                os.dup2(saved_fd, 2)
                _sync_windows_standard_error_handle()
            except OSError as error:
                restore_error = error
            finally:
                os.close(saved_fd)

        self._saved_fd = None
        self._active = False
        self._close_devnull()
        if restore_error is not None:
            raise restore_error

    def __enter__(self) -> "TerminalStderrGuard":
        """支持以上下文管理器持有 stderr 隔离。"""
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: types.TracebackType | None,
    ) -> None:
        """离开上下文时恢复 stderr。"""
        self.close()

    def _close_devnull(self) -> None:
        devnull = self._devnull
        self._devnull = None
        if devnull is not None:
            devnull.close()


def _stderr_targets_terminal() -> bool:
    """判断 stdout/stderr 是否都直接连接到当前交互终端。"""
    stdout_fd = _stream_fd(sys.stdout)
    stderr_fd = _stream_fd(sys.stderr)
    if stdout_fd is None or stderr_fd is None:
        return False

    try:
        if not os.isatty(stdout_fd) or not os.isatty(stderr_fd):
            return False
    except OSError:
        return False

    if sys.platform == "win32":
        # Windows 控制台的 stdout/stderr 可能对应两个不同的 HANDLE，
        # 但它们仍然绘制到同一可见终端；只要两者都是控制台就应隔离。
        return True

    try:
        return os.path.sameopenfile(stdout_fd, stderr_fd)
    except OSError:
        return False


def _stream_fd(stream: typing.TextIO) -> int | None:
    """安全取得文本流的文件描述符。"""
    try:
        return int(stream.fileno())
    except (AttributeError, OSError, ValueError):
        return None


def _sync_windows_standard_error_handle() -> None:
    """让 Windows 子进程继承与 Python fd 2 一致的 stderr HANDLE。"""
    if sys.platform != "win32":
        return None

    import ctypes
    import msvcrt

    standard_error = -12
    handle = msvcrt.get_osfhandle(2)
    result = ctypes.windll.kernel32.SetStdHandle(standard_error, handle)
    if not result:
        raise OSError("SetStdHandle(STD_ERROR_HANDLE) failed")


if __name__ == '__main__':
    pass
