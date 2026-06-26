# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path
from functools import lru_cache
from .models import SkillSpec
from .parser import parse_skill_frontmatter
from .paths import (
    bundled_skills_root,
    project_skills_roots,
    user_skills_root
)


def _load_skill(directory: Path, *, source: str) -> SkillSpec | None:
    """从目录读取 skill 元数据；无效目录返回空值。"""
    entry = directory / "SKILL.md"
    if not entry.is_file():
        return None

    metadata    = parse_skill_frontmatter(entry)
    name        = str(metadata.get("name") or directory.name).strip()
    description = str(metadata.get("description") or "").strip()

    if not name or not description:
        return None

    return SkillSpec(
        name=name,
        description=description,
        source=source,
        root=directory.resolve(),
        entry=entry.resolve()
    )


def _scan_skills(root: Path, *, source: str) -> tuple[SkillSpec, ...]:
    """扫描指定根目录下的一级 skill 目录。"""
    if not root.is_dir():
        return ()

    skills = [
        skill
        for directory in sorted(root.iterdir(), key=lambda item: item.name.lower())
        if directory.is_dir()
        if (skill := _load_skill(directory, source=source)) is not None
    ]
    return tuple(skills)


@lru_cache(maxsize=1)
def bundled_skills() -> tuple[SkillSpec, ...]:
    """读取随包提供的 skills。"""
    return _scan_skills(bundled_skills_root(), source="bundled")


@lru_cache(maxsize=1)
def user_skills() -> tuple[SkillSpec, ...]:
    """读取用户级 skills。"""
    return _scan_skills(user_skills_root(), source="user")


@lru_cache(maxsize=1)
def project_skills() -> tuple[SkillSpec, ...]:
    """读取项目级 skills。"""
    skills: list[SkillSpec] = []
    for root in project_skills_roots():
        skills.extend(_scan_skills(root, source="project"))
    return tuple(skills)


@lru_cache(maxsize=1)
def available_skills() -> tuple[SkillSpec, ...]:
    """返回按来源优先级去重后的可用 skills。"""
    by_name: dict[str, SkillSpec] = {}
    for skill in bundled_skills() + user_skills() + project_skills():
        by_name[skill.name] = skill

    source_order = {"project": 0, "user": 1, "bundled": 2}
    return tuple(
        sorted(
            by_name.values(),
            key=lambda item: (source_order.get(item.source, 99), item.name.lower())
        )
    )


if __name__ == '__main__':
    pass
