# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import typing
from pathlib import Path
from backend.utilities import const
from backend.utilities.storage.roots import (
    ensure_writable_file, storage_dir, temp_storage_dir
)

LOG_FILENAME    = f"{const.APP_NAME}.log"
TAIL_CHUNK_SIZE = 8192


def log_path() -> Path:
    """返回日志文件的标准存储路径。"""
    return storage_dir() / LOG_FILENAME


def ensure_log_path() -> Path:
    """返回可写日志文件路径，标准路径不可写时降级到临时目录。"""
    candidates = [log_path(), temp_storage_dir() / LOG_FILENAME]

    last_error: OSError | None = None
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        try:
            return ensure_writable_file(candidate)
        except OSError as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    raise PermissionError("no writable log file candidates")


def read_log_lines(max_lines: int = 400) -> dict[str, typing.Any]:
    """读取日志文件的尾部内容及基础统计信息。"""
    target = ensure_log_path()
    if not target.exists():
        return {
            "exists"     : False,
            "path"       : str(target),
            "lines"      : [],
            "line_count" : 0,
            "truncated"  : False,
            "size"       : 0
        }

    total  = _count_lines(target)
    sliced = _tail_lines(target, max_lines)

    return {
        "exists"     : True,
        "path"       : str(target),
        "lines"      : sliced,
        "line_count" : total,
        "truncated"  : total > len(sliced),
        "size"       : target.stat().st_size
    }


def _count_lines(target: Path) -> int:
    """统计目标日志文件中的总行数。"""
    total = 0
    with target.open("rb") as file:
        while chunk:=file.read(TAIL_CHUNK_SIZE):
            total += chunk.count(b"\n")

    if target.stat().st_size > 0:
        with target.open("rb") as file:
            file.seek(-1, os.SEEK_END)
            if file.read(1) != b"\n":
                total += 1

    return total


def _tail_lines(target: Path, max_lines: int) -> list[str]:
    """从日志文件尾部读取指定数量的行。"""
    wanted    = max(1, int(max_lines))
    collected = bytearray()
    newlines  = 0

    with target.open("rb") as file:
        file.seek(0, os.SEEK_END)
        position = file.tell()

        while position > 0 and newlines <= wanted:
            read_size = min(TAIL_CHUNK_SIZE, position)
            position -= read_size
            file.seek(position)
            chunk = file.read(read_size)
            collected[:0] = chunk
            newlines += chunk.count(b"\n")

    text = collected.decode(const.CHARSET, errors="replace")
    return text.splitlines()[-wanted:]


if __name__ == "__main__":
    pass
