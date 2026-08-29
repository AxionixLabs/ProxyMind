# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path


async def fetch_runtime_workspace_root() -> Path | None:
    """读取本地运行时工作区根目录。"""
    try:
        return Path.cwd().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


if __name__ == '__main__':
    pass
