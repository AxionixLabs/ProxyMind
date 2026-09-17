# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from pathlib import Path

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        """保存 Windows 文件锁要求的偏移和异步结构。"""

        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]
else:
    import fcntl


class FileLease:
    """持有跨进程共享或独占文件锁，关闭或进程退出时由操作系统释放。"""

    def __init__(self, path: Path, *, exclusive: bool) -> None:
        """打开稳定锁文件并尝试立即取得锁，冲突时不等待。"""
        self._file: typing.BinaryIO | None = path.open("a+b")
        try:
            if os.name == "nt":
                self._overlapped = _Overlapped()
                self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                self._kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                                    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
                self._kernel.LockFileEx.restype = wintypes.BOOL
                self._kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                                      wintypes.DWORD, ctypes.POINTER(_Overlapped)]
                self._kernel.UnlockFileEx.restype = wintypes.BOOL
                if not self._kernel.LockFileEx(msvcrt.get_osfhandle(self._file.fileno()),
                                               1 | (2 if exclusive else 0), 0, 1, 0,
                                               ctypes.byref(self._overlapped)):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_NB | (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH))
        except BaseException:
            self._file.close()
            self._file = None
            raise

    def close(self) -> None:
        """释放锁并关闭文件；锁文件保持原位以避免 inode 替换造成并发失锁。"""
        file = self._file
        if file is None:
            return
        try:
            if os.name == "nt":
                if not self._kernel.UnlockFileEx(msvcrt.get_osfhandle(file.fileno()), 0, 1, 0,
                                                 ctypes.byref(self._overlapped)):
                    raise ctypes.WinError(ctypes.get_last_error())
            else:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        finally:
            file.close()
            self._file = None


if __name__ == '__main__':
    pass
