# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path
from functools import lru_cache
from mind_core.config_session import ConfigSession
from mind_core.config_store import (
    ConfigStore,
    default_config_path
)
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


def _skill_names(values: object) -> frozenset[str]:
    """把配置中的 skill 名称列表规范化为小写集合。"""
    if not isinstance(values, list):
        return frozenset()
    return frozenset(
        name
        for raw in values
        if (name := str(raw or "").strip().lower())
    )


def _configured_skill_filters(config: dict | None = None) -> dict[str, list[str]]:
    """读取配置中的 skills 过滤规则。"""
    if config is None:
        try:
            config = ConfigSession(
                ConfigStore(default_config_path())
            ).load()
        except (OSError, TypeError, ValueError):
            config = {}

    skills = config.get("skills") if isinstance(config, dict) else {}

    if not isinstance(skills, dict):
        return {"enabled": [], "disabled": []}

    return {
        "enabled"  : list(skills.get("enabled") or []),
        "disabled" : list(skills.get("disabled") or [])
    }


def configured_skills(config: dict | None = None) -> tuple[SkillSpec, ...]:
    """返回应用配置过滤后的可用 skills。"""
    filters = _configured_skill_filters(config)
    return filter_skills(
        available_skills(),
        enabled=filters.get("enabled"),
        disabled=filters.get("disabled")
    )


def filter_skills(
    skills: tuple[SkillSpec, ...],
    *,
    enabled: object = None,
    disabled: object = None
) -> tuple[SkillSpec, ...]:
    """按 enabled 白名单和 disabled 黑名单过滤 skills。"""
    enabled_names  = _skill_names(enabled)
    disabled_names = _skill_names(disabled)

    filtered = []
    for skill in skills:
        name = skill.name.strip().lower()
        if enabled_names and name not in enabled_names:
            continue
        if name in disabled_names:
            continue
        filtered.append(skill)
    return tuple(filtered)


if __name__ == '__main__':
    pass
