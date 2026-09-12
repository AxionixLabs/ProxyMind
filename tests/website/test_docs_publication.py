import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from website.mind.scripts import (
    check_docs,
    sync_docs,
)


@pytest.fixture
def publication(tmp_path: Path) -> tuple[Path, Path, list[sync_docs.DocEntry]]:
    source_root = tmp_path / "source"
    site_root = tmp_path / "site"
    (source_root / "docs").mkdir(parents=True)
    site_root.mkdir()
    (source_root / "docs" / "README.md").write_text("# Index\n", encoding="utf-8")
    (source_root / "docs" / "guide.md").write_text(
        "# Guide\n[Usage](usage.md#budget)\n", encoding="utf-8",
    )
    (source_root / "docs" / "usage.md").write_text("# Usage\n", encoding="utf-8")
    entries = [
        sync_docs.DocEntry("docs/README.md", "docs-index.md", "Index"),
        sync_docs.DocEntry("docs/guide.md", "guide.md", "Guide"),
        sync_docs.DocEntry("docs/usage.md", "context-usage.md", "Usage"),
    ]
    (site_root / "docs_manifest.json").write_text(
        json.dumps({"entries": [asdict(entry) for entry in entries]}),
        encoding="utf-8",
    )
    return source_root, site_root, entries


def test_repository_preflight_uses_only_standard_library(repository_root: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-S", "website/mind/scripts/check_docs.py"],
        cwd=repository_root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_preflight_rejects_unlisted_document_even_without_links(publication) -> None:
    source_root, site_root, _entries = publication
    (source_root / "docs" / "new-topic.md").write_text("# New\n", encoding="utf-8")

    assert check_docs._validate_manifest(source_root, site_root) == [
        "document missing from manifest: docs/new-topic.md",
    ]
    assert not (site_root / "pages").exists()


@pytest.mark.parametrize("target_exists", [False, True])
def test_preflight_rejects_broken_or_unpublished_link(publication, target_exists: bool) -> None:
    source_root, site_root, _entries = publication
    (source_root / "docs" / "guide.md").write_text(
        "[Target](../unpublished.md)\n", encoding="utf-8",
    )
    if target_exists:
        (source_root / "unpublished.md").write_text("# Target\n", encoding="utf-8")

    prefix = "linked document missing from manifest" if target_exists else "broken source link"
    assert check_docs._validate_manifest(source_root, site_root) == [
        f"{prefix}: docs/guide.md -> ../unpublished.md",
    ]


def test_invalid_publication_preserves_existing_index_and_pages(publication) -> None:
    source_root, site_root, entries = publication
    generated = site_root / "pages" / "generated"
    generated.mkdir(parents=True)
    previous = generated / "previous.md"
    previous.write_text("previous page\n", encoding="utf-8")
    index = source_root / "docs" / "README.md"
    before = index.read_bytes()
    entries.append(sync_docs.DocEntry("missing.md", "missing.md", "Missing"))

    with pytest.raises(ValueError, match="manifest source missing: missing.md"):
        sync_docs.sync_reference_docs(source_root, site_root, entries)

    assert index.read_bytes() == before
    assert previous.read_text(encoding="utf-8") == "previous page\n"


def test_sync_rebuilds_stale_index_and_validates_generated_links(publication) -> None:
    source_root, site_root, entries = publication
    (source_root / "docs" / "README.md").write_text(
        "[Retired](retired.md)\n", encoding="utf-8",
    )
    guide = source_root / "docs" / "guide.md"
    guide.write_text(
        "[Usage](usage.md#budget)\n"
        "[External](https://example.com/remote.md)\n"
        "[External](//example.com/remote.md)\n",
        encoding="utf-8",
    )

    sync_docs.sync_reference_docs(source_root, site_root, entries)

    assert check_docs._validate_generated_pages(site_root) == []
    assert "retired.md" not in (source_root / "docs" / "README.md").read_text(encoding="utf-8")
    generated = site_root / "pages" / "generated" / "guide.md"
    assert "[Usage](context-usage.md#budget)" in generated.read_text(encoding="utf-8")
    generated.write_text("[Missing](absent.md)\n", encoding="utf-8")
    assert check_docs._validate_generated_pages(site_root) == [
        "broken generated link: guide.md -> absent.md",
    ]


def test_sync_runs_from_public_bundle_without_application_sources(
    publication, tmp_path: Path, repository_root: Path,
) -> None:
    source_root, site_root, _entries = publication
    bundle = tmp_path / "SoftwareCenter"
    public_source = bundle / "Assets" / "Mind"
    public_site = bundle / "site" / "mind"
    shutil.copytree(source_root, public_source)
    shutil.copytree(site_root, public_site)
    scripts = public_site / "scripts"
    scripts.mkdir()
    shutil.copy2(repository_root / "website" / "mind" / "scripts" / "sync_docs.py", scripts)

    result = subprocess.run(
        [sys.executable, "-S", str(scripts / "sync_docs.py")],
        cwd=public_site, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (public_site / "pages" / "generated" / "context-usage.md").is_file()
    assert check_docs._validate_generated_pages(public_site) == []


def test_default_check_reports_publication_error_before_generation(publication) -> None:
    source_root, site_root, _entries = publication
    (source_root / "docs" / "extra.md").write_text("# Extra\n", encoding="utf-8")
    with (
        patch.object(check_docs, "resolve_roots", return_value=(source_root, site_root)),
        patch.object(check_docs, "_slash_command_names", return_value=()),
        patch.object(check_docs, "_cli_command_paths", return_value=()),
        patch.object(check_docs, "_missing_tokens", return_value=()),
    ):
        errors = check_docs.validate()

    assert errors == ("document missing from manifest: docs/extra.md",)
