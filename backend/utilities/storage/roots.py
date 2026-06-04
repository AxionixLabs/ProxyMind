# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import sys
import tempfile
import contextlib
from pathlib import Path
from backend.utilities.paths import app_root
from backend.utilities import const

APP_DATA_DIR_NAME = f"{const.APP_DESC}"
DATA_STORAGE_DIR  = r"storage"
DATA_OUTPUT_DIR   = r"outputs"
STORAGE_ENV_NAME  = f"{const.APP_NAME.upper()}_STORAGE_ROOT"


def platform_data_root() -> Path:
    """按平台返回首选应用数据根目录。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DATA_DIR_NAME

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DATA_DIR_NAME

    return app_root() / APP_DATA_DIR_NAME


def temp_data_root() -> Path:
    """返回临时目录下的应用数据根目录。"""
    return Path(tempfile.gettempdir()) / APP_DATA_DIR_NAME


def candidate_data_roots() -> list[Path]:
    """返回按优先级排列的数据根目录候选。"""
    candidates: list[Path] = []
    if raw := os.environ.get(STORAGE_ENV_NAME):
        candidates.append(Path(raw).expanduser())
    candidates.extend(
        [platform_data_root(), app_root() / APP_DATA_DIR_NAME, temp_data_root()]
    )

    return dedupe_paths(candidates)


def dedupe_paths(paths: list[Path]) -> list[Path]:
    """按字符串路径去重并保持候选顺序。"""
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


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


def first_writable_dir(candidates: list[Path]) -> Path:
    """从候选目录中选择第一个可写目录。"""
    last_error: OSError | None = None
    for candidate in dedupe_paths(candidates):
        try:
            return ensure_writable_dir(candidate)
        except OSError as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    raise PermissionError("no writable storage directory candidates")


def data_root() -> Path:
    """返回可写应用数据根目录，必要时降级到临时目录。"""
    return first_writable_dir(candidate_data_roots())


def storage_dir() -> Path:
    """返回可写 storage 目录。"""
    return first_writable_dir(
        [data_root() / DATA_STORAGE_DIR, temp_data_root() / DATA_STORAGE_DIR]
    )


def temp_storage_dir() -> Path:
    """返回临时目录下的可写 storage 目录。"""
    return ensure_writable_dir(temp_data_root() / DATA_STORAGE_DIR)


def output_base_dir(output_dir: str | None = None) -> Path:
    """返回可写输出根目录，首选用户传入路径，失败时降级到 storage/outputs。"""
    candidates: list[Path] = []

    if output_dir:
        candidates.append(Path(output_dir).expanduser().resolve())
    candidates.extend(
        [storage_dir() / DATA_OUTPUT_DIR, temp_storage_dir() / DATA_OUTPUT_DIR]
    )

    return first_writable_dir(candidates)


if __name__ == "__main__":
    pass
