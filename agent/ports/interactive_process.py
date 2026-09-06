# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import (
    AsyncIterator,
    Mapping
)
from dataclasses import (
    dataclass,
    field
)
from pathlib import Path
from types import MappingProxyType

__all__ = (
    "InteractiveProcessCapability",
    "InteractiveProcessHandle",
    "InteractiveProcessSpec",
    "TerminalSize",
)


@dataclass(frozen=True, slots=True)
class TerminalSize:
    """描述交互式进程使用的终端字符尺寸。"""

    rows: int = 24
    columns: int = 80

    def __post_init__(self) -> None:
        """拒绝原生终端无法表示的行列值。"""
        if isinstance(self.rows, bool) or not isinstance(self.rows, int):
            raise TypeError("terminal rows must be an integer")
        if isinstance(self.columns, bool) or not isinstance(self.columns, int):
            raise TypeError("terminal columns must be an integer")
        if not 1 <= self.rows <= 32767:
            raise ValueError("terminal rows must be between 1 and 32767")
        if not 1 <= self.columns <= 32767:
            raise ValueError("terminal columns must be between 1 and 32767")


@dataclass(frozen=True, slots=True)
class InteractiveProcessSpec:
    """冻结一次本机交互式进程的启动参数。"""

    argv: tuple[str, ...]
    cwd: str | Path
    env: Mapping[str, str] = field(default_factory=dict)
    size: TerminalSize = TerminalSize()

    def __post_init__(self) -> None:
        """校验启动参数并冻结环境快照。"""
        if isinstance(self.argv, (str, bytes)):
            raise TypeError("interactive process argv must be a sequence")
        argv = tuple(self.argv)
        if not argv or not isinstance(argv[0], str) or not argv[0].strip():
            raise ValueError("interactive process argv is required")
        if any(not isinstance(item, str) or not item for item in argv):
            raise TypeError("interactive process argv must contain strings")
        cwd = str(self.cwd or "").strip()
        if not cwd:
            raise ValueError("interactive process cwd is required")
        if not isinstance(self.env, Mapping):
            raise TypeError("interactive process env must be an object")
        env: dict[str, str] = {}
        for key, value in self.env.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("interactive process env contains an empty name")
            if not isinstance(value, str):
                raise TypeError("interactive process env values must be strings")
            env[key.strip()] = value
        if not isinstance(self.size, TerminalSize):
            raise TypeError("interactive process size must be TerminalSize")
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "env", MappingProxyType(env))


@typing.runtime_checkable
class InteractiveProcessHandle(typing.Protocol):
    """定义单个原生 PTY 进程的异步生命周期。

    实现方独占 PTY 和子进程树，输出流只允许一个消费者；`interrupt` 必须发送
    终端 Ctrl-C，不能退化为终止进程。全部关闭操作必须可以重复调用。
    """

    session_id: str
    pid: int | None
    returncode: int | None

    def read_output(self) -> AsyncIterator[bytes]:
        """持续读取合并后的终端原始输出。"""
        ...

    async def write(self, data: str, *, eof: bool = False) -> None:
        """写入 UTF-8 文本，并可选交付终端 EOF。"""
        ...

    async def interrupt(self) -> None:
        """通过终端向前台进程发送 Ctrl-C。"""
        ...

    async def resize(self, size: TerminalSize) -> None:
        """修改当前原生终端尺寸。"""
        ...

    async def wait(self) -> int:
        """等待根进程退出、排空终端并返回退出码。"""
        ...

    async def terminate(self, *, force: bool = False) -> None:
        """终止或强制终止该句柄拥有的完整进程树。"""
        ...

    async def aclose(self) -> None:
        """幂等关闭终端句柄并回收完整进程树。"""
        ...


@typing.runtime_checkable
class InteractiveProcessCapability(typing.Protocol):
    """创建本机原生 PTY 进程并统一回收其生命周期。"""

    async def spawn(
        self,
        spec: InteractiveProcessSpec,
    ) -> InteractiveProcessHandle:
        """按冻结启动参数创建并登记交互式进程。"""
        ...

    async def aclose(self) -> None:
        """回收该能力创建的全部交互式进程。"""
        ...


if __name__ == '__main__':
    pass
