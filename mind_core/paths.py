# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
from pathlib import Path
from mind_nova import const


def resolve_mind_work(
    entry_file: str | None = None,
    *,
    strict: bool = False
) -> Path:
    """返回 Mind 运行资源根目录。"""
    software = Path(sys.argv[0]).name.strip().lower()

    if software == f"{const.APP_NAME}.exe":
        return Path(sys.argv[0]).resolve().parent

    if software == const.APP_NAME:
        return Path(sys.executable).resolve().parent

    if software == f"{const.APP_NAME}.py":
        return Path(entry_file or __file__).resolve().parent

    if strict:
        raise ValueError(f"{const.APP_DESC} compatible with {const.APP_NAME} command")

    return Path.cwd().resolve()


def schematic_root(
    entry_file: str | None = None,
    *,
    strict: bool = False
) -> Path:
    """返回 Mind schematic 资源根目录。"""
    return resolve_mind_work(entry_file, strict=strict) / const.SCHEMATIC


if __name__ == '__main__':
    pass
