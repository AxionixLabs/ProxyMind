# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import contextlib
from pathlib import Path
from backend.utilities import const

MD_HOME_ENV = "MIND_HOME"
HX_HOME_ENV = "HELIX_HOME"

DATA_STORAGE_DIR = r"storage"
DATA_OUTPUT_DIR  = r"outputs"
STORAGE_ENV_NAME = f"{const.APP_NAME.upper()}_STORAGE_ROOT"


def application_home() -> Path:
    """返回应用的用户级统一目录。"""
    return Path(os.environ.get(MD_HOME_ENV) or Path.home() / ".mind").expanduser()


def helix_home() -> Path:
    """返回 Helix 在统一目录协议下的数据根。"""
    return Path(os.environ.get(HX_HOME_ENV) or application_home() / const.APP_NAME).expanduser()


def platform_data_root() -> Path:
    """返回首选应用数据根目录。"""
    return helix_home()


def candidate_data_roots() -> list[Path]:
    """返回数据根目录候选；仅保留显式覆盖或统一默认目录。"""
    if raw := os.environ.get(STORAGE_ENV_NAME):
        return [Path(raw).expanduser()]
    return [platform_data_root()]


def ensure_writable_dir(path: Path) -> Path:
    """确保目录存在且可写，并返回目录路径。"""
    target = Path(path).expanduser()
    target.mkdir(parents=True, exist_ok=True)

    probe = target / f".write_probe_{os.getpid()}_{id(target)}"
    with probe.open("w", encoding=const.CHARSET) as file:
        file.write("")
    with contextlib.suppress(OSError):
        probe.unlink()

    return target


def ensure_writable_file(path: Path) -> Path:
    """确保文件所在目录和文件本身可写，并返回文件路径。"""
    target = Path(path).expanduser()
    ensure_writable_dir(target.parent)

    with target.open("a+b"):
        pass

    return target


def data_root() -> Path:
    """返回可写应用数据根目录；不可写时直接抛错。"""
    return ensure_writable_dir(candidate_data_roots()[0])


def storage_dir() -> Path:
    """返回可写 storage 目录。"""
    return ensure_writable_dir(data_root() / DATA_STORAGE_DIR)


def output_base_dir(output_dir: str | None = None) -> Path:
    """返回可写输出根目录；不可写时直接抛错。"""
    if output_dir:
        return ensure_writable_dir(Path(output_dir).expanduser().resolve())
    return ensure_writable_dir(storage_dir() / DATA_OUTPUT_DIR)


if __name__ == "__main__":
    pass
