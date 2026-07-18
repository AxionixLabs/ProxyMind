# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
from pathlib import Path
from backend.utilities import const

APP_ENTRY_NAMES    = {const.APP_NAME, f"{const.APP_NAME}.exe"}
SCRIPT_ENTRY_NAMES = {f"{const.APP_NAME}.py"}


def app_root() -> Path:
    """推断当前应用入口所在根目录。"""
    software = Path(sys.argv[0]).name.strip().lower()

    if software in APP_ENTRY_NAMES:
        return Path(sys.argv[0]).resolve().parent

    if software in SCRIPT_ENTRY_NAMES:
        return Path(__file__).resolve().parents[2]

    return Path.cwd()


def resource_path(*parts: str) -> Path:
    """
    返回运行时资源路径。

    打包入口优先命中分发根目录；源码模式优先命中 backend 目录。
    """
    root           = app_root()
    direct         = root.joinpath(*parts)
    backend_scoped = root.joinpath("backend", *parts)

    software = Path(sys.argv[0]).name.strip().lower()
    if software in APP_ENTRY_NAMES and direct.exists():
        return direct

    if backend_scoped.exists():
        return backend_scoped

    if direct.exists():
        return direct

    return direct


if __name__ == "__main__":
    pass
