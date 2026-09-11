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
        lambda workspace: (_skill("REVIEW", "project"),),
    )
    registry.available_skills.cache_clear()

    try:
        skills = registry.available_skills(Path("workspace"))
    finally:
        registry.available_skills.cache_clear()

    assert [(skill.name, skill.source) for skill in skills] == [
        ("REVIEW", "project"),
    ]


def test_project_skill_cache_is_scoped_to_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "bundled_skills", lambda: ())
    monkeypatch.setattr(registry, "user_skills", lambda: ())
    monkeypatch.setattr(registry, "project_skills_roots", lambda root: (root / "skills",))
    for name in ("a", "b"):
        entry = tmp_path / name / "skills" / name / "SKILL.md"
        entry.parent.mkdir(parents=True)
        entry.write_text(f"---\nname: {name}\ndescription: Project {name}\n---\n", encoding="utf-8")
    assert [item.name for item in registry.available_skills(tmp_path / "a")] == ["a"]
    assert [item.name for item in registry.available_skills(tmp_path / "b")] == ["b"]
    assert [item.name for item in registry.available_skills(tmp_path / "a")] == ["a"]
