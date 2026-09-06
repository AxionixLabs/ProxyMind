# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from pathlib import Path

from agent.ports.interactive_process import TerminalSize
from infrastructure.platform.pty.contract import NativePtyBackend

__all__ = ("spawn_native_pty",)


def spawn_native_pty(
    argv: typing.Sequence[str],
    *,
    cwd: Path,
    env: typing.Mapping[str, str],
    size: TerminalSize,
) -> NativePtyBackend:
    """选择当前平台的原生 PTY adapter 并创建进程。"""
    if not argv:
        raise ValueError("interactive process argv must not be empty")
    if os.name == "nt":
        from infrastructure.platform.pty.windows import WindowsPtyBackend

        return WindowsPtyBackend(argv, cwd=cwd, env=env, size=size)
    if os.name == "posix":
        from infrastructure.platform.pty.posix import PosixPtyBackend

        return PosixPtyBackend(argv, cwd=cwd, env=env, size=size)
    raise RuntimeError(f"native PTY is not supported on platform: {os.name}")


if __name__ == '__main__':
    pass
