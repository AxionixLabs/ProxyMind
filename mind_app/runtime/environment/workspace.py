# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from pathlib import Path
from .exec_env import exec_env


async def fetch_runtime_workspace_root() -> typing.Optional[Path]:
    """读取本地运行时工作区根目录。"""
    data = exec_env()
    workspace = data.get("workspace") if isinstance(data, dict) else None

    root = workspace.get("root") if isinstance(workspace, dict) else None
    if not isinstance(root, str) or not root.strip():
        root = str(Path.cwd())

    try:
        return Path(root).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


if __name__ == '__main__':
    pass
