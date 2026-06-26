# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
from pathlib import Path
from mind_nova import const

AGENTS_DIR = ".agents"
SKILLS_DIR = "skills"


def _mind_work() -> Path:
    software = Path(sys.argv[0]).name.strip().lower()

    if software == f"{const.APP_NAME}.exe":
        return Path(sys.argv[0]).resolve().parent

    if software == const.APP_NAME:
        return Path(sys.executable).resolve().parent

    if software == f"{const.APP_NAME}.py":
        return Path(sys.argv[0]).resolve().parent

    return Path.cwd().resolve()


def bundled_skills_root() -> Path:
    """返回内置 skills 根目录。"""
    return _mind_work() / const.SCHEMATIC / "skills" / "bundled"


def _repo_root(start: Path) -> Path:
    """返回包含 .git 标记的仓库根目录；不存在时返回起点。"""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def project_skills_roots(start: Path | None = None) -> tuple[Path, ...]:
    """返回从仓库根到当前目录的项目级 skills 根目录。"""
    current = (start or Path.cwd()).resolve()
    root    = _repo_root(current)

    directories: list[Path] = []
    cursor = current
    while True:
        directories.append(cursor)
        if cursor == root:
            break
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent

    return tuple(
        directory / AGENTS_DIR / SKILLS_DIR
        for directory in reversed(directories)
    )


def user_skills_root() -> Path:
    """返回当前用户的 skills 根目录。"""
    return Path.home().expanduser() / AGENTS_DIR / SKILLS_DIR


if __name__ == '__main__':
    pass
