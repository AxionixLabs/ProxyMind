# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only - keep it private.

import shutil
import typing
from functools import lru_cache


class EnvironmentCapability(typing.TypedDict):
    """描述 Helix 对客户端公开的单项能力。"""

    available: bool
    command: str | None
    executable: str | None
    path: str | None
    version: str | None
    source: str | None


class EnvironmentProvider(typing.TypedDict):
    """描述 Helix 提供方的固定环境协议结构。"""

    tools: dict[str, EnvironmentCapability]
    extensions: dict[str, typing.Any]


@lru_cache(maxsize=1)
def _cached_exec_env() -> EnvironmentProvider:
    """返回缓存的 Helix 能力提供方事实。"""
    return EnvironmentProvider(
        tools={
            name: tool_bin(name)
            for name in ("adb", "k6", "ffmpeg", "ffprobe", "framix", "memrix")
        },
        extensions={},
    )


def exec_env() -> EnvironmentProvider:
    """返回 Helix 能力提供方事实。"""
    return _cached_exec_env()


def tool_bin(command: str) -> EnvironmentCapability:
    """返回单个 Helix 命令行工具的固定能力结构。"""
    executable = shutil.which(command)
    return EnvironmentCapability(
        available=executable is not None,
        command=command,
        executable=executable,
        path=executable,
        version=None,
        source="provider" if executable else "missing",
    )


if __name__ == '__main__':
    pass
