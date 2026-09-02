# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
from pathlib import Path

FRONTMATTER_RE = re.compile(
    r"^---\s*\r?\n(?P<meta>.*?)\r?\n---\s*(?:\r?\n|$)", re.DOTALL
)


def parse_skill_frontmatter(path: Path) -> dict[str, str]:
    """读取技能文档的头部元数据。"""
    text = path.read_text(encoding="utf-8", errors="replace")

    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}

    metadata: dict[str, str] = {}
    for raw_line in match.group("meta").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue

        key, value = line.split(":", 1)

        normalized_key = key.strip()
        normalized_value = value.strip().strip('"').strip("'")

        if normalized_key:
            metadata[normalized_key] = normalized_value

    return metadata


if __name__ == '__main__':
    pass
