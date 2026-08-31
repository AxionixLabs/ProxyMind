# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass

__all__ = (
    "ServiceRuntimeContext",
    "ServiceRuntimeSpec",
)


@dataclass(frozen=True, slots=True)
class ServiceRuntimeSpec:
    """描述本地服务运行时的路径和启动命令。"""

    supports: str
    executable: str
    launch_command: list[str]
    path_entries: tuple[str, ...]
    working_directory: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceRuntimeContext:
    """描述服务运行时在当前入口下的准备参数。"""

    spec: ServiceRuntimeSpec
    platform: str
    packaged: bool
    env_symbol: str
    app_desc: str


if __name__ == '__main__':
    pass
