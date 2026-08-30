# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path
from infrastructure.config.paths import resolve_application_layout

AGENTS_DIR = ".agents"
SKILLS_DIR = "skills"


def _mind_work() -> Path:
    try:
        return resolve_application_layout().root
    except ValueError:
        return Path.cwd().resolve()


def bundled_skills_root() -> Path:
    """返回内置 skills 根目录。"""
    return _mind_work() / "schematic" / "skills" / "bundled"


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
