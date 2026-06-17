# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mind_core.paths import resolve_mind_work, schematic_root
from mind_core.skills import available_skills, bundled_skills, skills_payload
from mind_core.skills import registry as skills_registry
from mind_core.skills.models import SkillSpec
from mind_core.skills.parser import parse_skill_frontmatter
from mind_nova.requests.payload import ensure_default_skills


class SkillsTest(unittest.TestCase):

    def tearDown(self) -> None:
        skills_registry.bundled_skills.cache_clear()
        skills_registry.user_skills.cache_clear()
        skills_registry.project_skills.cache_clear()
        skills_registry.available_skills.cache_clear()

    def _write_skill(
        self,
        root: Path,
        folder: str,
        *,
        name: str,
        description: str,
    ) -> Path:
        skill_root = root / folder
        skill_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "---\n\n"
            f"# {name}\n",
            encoding="utf-8",
        )
        return skill_root

    def test_mind_work_defaults_to_cwd_for_dev_tools(self) -> None:
        with patch("sys.argv", ["python"]):
            self.assertEqual(resolve_mind_work(), Path.cwd())

    def test_mind_work_source_entry_uses_entry_file_parent(self) -> None:
        entry = Path.cwd() / "mind.py"
        with patch("sys.argv", [str(entry)]):
            self.assertEqual(resolve_mind_work(str(entry)), Path.cwd())
            self.assertEqual(schematic_root(str(entry)), Path.cwd() / "schematic")

    def test_mind_work_packaged_windows_uses_exe_parent(self) -> None:
        exe = Path.cwd() / "dist" / "mind.exe"
        with patch("sys.argv", [str(exe)]):
            self.assertEqual(resolve_mind_work(), exe.parent)

    def test_mind_work_packaged_posix_uses_python_executable_parent(self) -> None:
        base = Path.cwd() / "tmp" / "Mind.app" / "Contents" / "MacOS"
        launcher = base / "mind"
        executable = base / "python"
        with (
            patch("sys.argv", [str(launcher)]),
            patch("sys.executable", str(executable)),
        ):
            self.assertEqual(resolve_mind_work(), executable.parent)

    def test_bundled_skills_finds_mind_docs(self) -> None:
        skills = bundled_skills()
        names = [skill.name for skill in skills]

        self.assertIn("mind-docs", names)

        skill = next(skill for skill in skills if skill.name == "mind-docs")
        self.assertEqual(skill.source, "bundled")
        self.assertTrue(skill.entry.name == "SKILL.md")
        self.assertTrue(skill.entry.exists())
        self.assertTrue((skill.root / "references" / "README.md").exists())

    def test_available_skills_loads_project_user_and_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled_root = root / "bundled"
            user_root = root / "user"
            project_root = root / "project"

            self._write_skill(bundled_root, "bundled-skill", name="bundled-skill", description="Bundled skill")
            self._write_skill(user_root, "user-skill", name="user-skill", description="User skill")
            self._write_skill(project_root, "project-skill", name="project-skill", description="Project skill")

            with (
                patch("mind_core.skills.registry.bundled_skills_root", return_value=bundled_root),
                patch("mind_core.skills.registry.user_skills_root", return_value=user_root),
                patch("mind_core.skills.registry.project_skills_root", return_value=project_root),
            ):
                self.tearDown()
                skills = available_skills()

        sources = {skill.name: skill.source for skill in skills}
        self.assertEqual(sources["bundled-skill"], "bundled")
        self.assertEqual(sources["user-skill"], "user")
        self.assertEqual(sources["project-skill"], "project")
        scopes = {skill.name: skill.payload()["scope"] for skill in skills}
        self.assertEqual(scopes["bundled-skill"], "global")
        self.assertEqual(scopes["user-skill"], "user")
        self.assertEqual(scopes["project-skill"], "repo")

    def test_available_skills_prefers_project_then_user_then_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled_root = root / "bundled"
            user_root = root / "user"
            project_root = root / "project"

            self._write_skill(bundled_root, "shared", name="shared", description="Bundled shared")
            self._write_skill(user_root, "shared", name="shared", description="User shared")
            self._write_skill(project_root, "shared", name="shared", description="Project shared")

            with (
                patch("mind_core.skills.registry.bundled_skills_root", return_value=bundled_root),
                patch("mind_core.skills.registry.user_skills_root", return_value=user_root),
                patch("mind_core.skills.registry.project_skills_root", return_value=project_root),
            ):
                self.tearDown()
                skills = available_skills()

        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "shared")
        self.assertEqual(skills[0].source, "project")
        self.assertEqual(skills[0].description, "Project shared")

    def test_missing_skill_roots_load_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            self.assertEqual(
                skills_registry._scan_skills(missing, source="bundled"),
                (),
            )

    def test_scan_skips_directories_without_valid_skill_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            (root / "no-entry").mkdir()
            (root / "no-frontmatter").mkdir()
            (root / "no-frontmatter" / "SKILL.md").write_text("# Missing metadata\n", encoding="utf-8")
            self._write_skill(root, "empty-description", name="empty-description", description="")
            self._write_skill(root, "valid", name="valid", description="Usable skill")

            skills = skills_registry._scan_skills(root, source="user")

        self.assertEqual([skill.name for skill in skills], ["valid"])

    def test_skill_frontmatter_parses_quotes_comments_and_colon_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / "SKILL.md"
            entry.write_text(
                "---\n"
                "# comment\n"
                "name: 'quoted-name'\n"
                "description: \"Use when values include http://localhost:8000 paths\"\n"
                "ignored line\n"
                "---\n\n"
                "body\n",
                encoding="utf-8",
            )

            metadata = parse_skill_frontmatter(entry)

        self.assertEqual(metadata["name"], "quoted-name")
        self.assertEqual(
            metadata["description"],
            "Use when values include http://localhost:8000 paths",
        )
        self.assertNotIn("ignored line", metadata)

    def test_skill_payload_maps_unknown_source_without_failure(self) -> None:
        spec = SkillSpec(
            name="custom",
            description="Custom skill",
            source="team",
            root=Path("custom"),
            entry=Path("custom") / "SKILL.md",
        )

        self.assertEqual(spec.payload()["scope"], "team")

    def test_skills_payload_is_metadata_only(self) -> None:
        payload = skills_payload()

        self.assertIsInstance(payload, list)
        self.assertTrue(payload)

        first = payload[0]
        self.assertIn("name", first)
        self.assertIn("description", first)
        self.assertIn("path", first)
        self.assertIn("scope", first)
        self.assertNotIn("content", first)
        self.assertNotIn("references", first)
        self.assertNotIn("root", first)
        self.assertNotIn("entry", first)

    def test_request_defaults_skills_when_missing_none_or_empty(self) -> None:
        for kwargs in ({}, {"skills": None}, {"skills": []}):
            with self.subTest(kwargs=kwargs):
                ensure_default_skills(kwargs)

                self.assertTrue(kwargs["skills"])
                self.assertEqual(kwargs["skills"][0]["name"], "mind-docs")

    def test_request_keeps_explicit_non_empty_skills(self) -> None:
        explicit = [{"name": "custom", "description": "", "path": "", "scope": "repo"}]
        kwargs = {"skills": explicit}

        ensure_default_skills(kwargs)

        self.assertIs(kwargs["skills"], explicit)


if __name__ == "__main__":
    unittest.main()
