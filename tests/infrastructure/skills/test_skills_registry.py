# -*- coding: utf-8 -*-

from pathlib import Path

from infrastructure.skills import registry
from infrastructure.skills import SkillSpec


def _skill(name: str, source: str) -> SkillSpec:
    """创建 registry 去重测试使用的 skill 描述。"""
    root = Path(source) / name
    return SkillSpec(
        name=name,
        description=f"Use {name}",
        source=source,
        root=root,
        entry=root / "SKILL.md",
    )


def test_available_skills_deduplicates_names_case_insensitively(monkeypatch) -> None:
    monkeypatch.setattr(
        registry,
        "bundled_skills",
        lambda: (_skill("Review", "bundled"),),
    )
    monkeypatch.setattr(
        registry,
        "user_skills",
        lambda: (_skill("review", "user"),),
    )
    monkeypatch.setattr(
        registry,
        "project_skills",
        lambda: (_skill("REVIEW", "project"),),
    )
    registry.available_skills.cache_clear()

    try:
        skills = registry.available_skills()
    finally:
        registry.available_skills.cache_clear()

    assert [(skill.name, skill.source) for skill in skills] == [
        ("REVIEW", "project"),
    ]
