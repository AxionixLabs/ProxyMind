# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import os
import sys
import typing
from pathlib import Path
from backend.utilities import const

APP_ENTRY_NAMES    = {const.APP_NAME, f"{const.APP_NAME}.exe"}
SCRIPT_ENTRY_NAMES = {f"{const.APP_NAME}.py"}
APP_DATA_DIR_NAME  = f"{const.APP_DESC}"
DATA_STORAGE_DIR   = f"storage"
LOG_FILENAME       = f"{const.APP_NAME}.log"
TAIL_CHUNK_SIZE    = 8192


def _app_root() -> Path:
    software = Path(sys.argv[0]).name.strip().lower()

    if software in APP_ENTRY_NAMES:
        return Path(sys.argv[0]).resolve().parent

    if software in SCRIPT_ENTRY_NAMES:
        return Path(__file__).resolve().parents[3]

    return Path.cwd()


def _data_root() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DATA_DIR_NAME

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_DATA_DIR_NAME

    return _app_root() / APP_DATA_DIR_NAME


def log_path() -> Path:
    return _data_root() / DATA_STORAGE_DIR / LOG_FILENAME


def ensure_log_path() -> Path:
    target = log_path()
    os.makedirs(target.parent, exist_ok=True)
    return target


def _count_lines(target: Path) -> int:
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
    wanted = max(1, int(max_lines))
    collected = bytearray()
    newlines = 0

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


def read_log_lines(max_lines: int = 400) -> dict[str, typing.Any]:
    target = ensure_log_path()
    if not target.exists():
        return {
            "exists"     : False,
            "path"       : str(target),
            "lines"      : [],
            "line_count" : 0,
            "truncated"  : False,
            "size"       : 0,
        }

    total  = _count_lines(target)
    sliced = _tail_lines(target, max_lines)

    return {
        "exists"     : True,
        "path"       : str(target),
        "lines"      : sliced,
        "line_count" : total,
        "truncated"  : total > len(sliced),
        "size"       : target.stat().st_size,
    }


if __name__ == "__main__":
    pass
