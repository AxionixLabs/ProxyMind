from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_README = ROOT / "README.md"
SOURCE_DOCS = ROOT / "docs"
TARGET_ROOT = ROOT / ".site-docs"
TARGET_DOCS = TARGET_ROOT / "docs"


def reset_target() -> None:
    if TARGET_ROOT.exists():
        shutil.rmtree(TARGET_ROOT)
    TARGET_DOCS.mkdir(parents=True, exist_ok=True)


def copy_root_readme() -> None:
    shutil.copy2(SOURCE_README, TARGET_ROOT / "index.md")


def copy_docs_tree() -> None:
    for path in sorted(SOURCE_DOCS.glob("*.md")):
        shutil.copy2(path, TARGET_DOCS / path.name)


def main() -> None:
    reset_target()
    copy_root_readme()
    copy_docs_tree()
    print(f"Generated site docs in {TARGET_ROOT}")


if __name__ == "__main__":
    main()
