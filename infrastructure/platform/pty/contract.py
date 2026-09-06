# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.ports.interactive_process import TerminalSize

__all__ = (
    "NativePtyBackend",
    "PtyEndOfFile",
)


class PtyEndOfFile(Exception):
    """表示原生伪终端的输出已经完整关闭。"""


class NativePtyBackend(typing.Protocol):
    """定义同步原生 PTY 的内部平台边界。

    实现方拥有原生终端与进程树；阻塞调用只允许由异步 capability 的线程边界
    驱动，`close` 必须解除正在进行的读取并可重复调用。
    """

    @property
    def pid(self) -> int:
        """返回根子进程标识。"""
        ...

    def read(self, size: int) -> bytes:
        """阻塞读取原始终端字节，关闭时抛出 `PtyEndOfFile`。"""
        ...

    def write(self, data: bytes) -> int:
        """完整写入原生终端输入。"""
        ...

    def close_input(self) -> None:
        """向终端交付 EOF 并拒绝后续输入。"""
        ...

    def resize(self, size: TerminalSize) -> None:
        """更新原生终端行列。"""
        ...

    def interrupt(self) -> None:
        """向终端前台进程发送 Ctrl-C。"""
        ...

    def is_alive(self) -> bool:
        """判断根子进程是否仍在运行。"""
        ...

    def wait(self) -> int:
        """取得已退出根进程的退出码并关闭终端输出。"""
        ...

    def terminate(self) -> None:
        """终止所拥有的进程树。"""
        ...

    def kill(self) -> None:
        """强制终止所拥有的进程树。"""
        ...

    def close(self) -> None:
        """幂等释放终端、进程与平台句柄。"""
        ...


if __name__ == '__main__':
    pass
