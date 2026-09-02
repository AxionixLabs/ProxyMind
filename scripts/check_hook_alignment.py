# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "protocol" / "hook_alignment_manifest.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_manifest() -> dict[str, object]:
    """读取并校验固定 Hook 对齐清单。"""
    with MANIFEST_PATH.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("hook alignment manifest must be an object")
    return value


def _sha256(path: Path) -> str:
    """计算基线文件的 SHA-256 摘要。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _schema_sha256(value: object) -> str:
    """计算结构化 schema 的稳定摘要。"""
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check() -> None:
    """校验本地 Hook 目录、schema 和固定上游文件清单。"""
    manifest = _load_manifest()

    from agent.application.hooks.events import HOOK_EVENT_SPECS
    from agent.application.hooks.protocol import (
        HOOK_INPUT_SCHEMAS,
        HOOK_OUTPUT_SCHEMAS,
    )
    from agent.domain.hooks import (
        HOOK_EVENT_CONFIG_SPECS,
        HOOK_EVENT_NAMES,
    )

    events = manifest.get("events")
    if not isinstance(events, list):
        raise ValueError("manifest events must be an array")
    manifest_names = tuple(
        item.get("name")
        for item in events
        if isinstance(item, dict)
    )
    if manifest_names != HOOK_EVENT_NAMES:
        raise ValueError(
            f"event catalog mismatch: manifest={manifest_names!r}, "
            f"local={HOOK_EVENT_NAMES!r}"
        )
    if set(HOOK_EVENT_CONFIG_SPECS) != set(HOOK_EVENT_NAMES):
        raise ValueError("domain event specifications are incomplete")
    if tuple(HOOK_EVENT_SPECS) != HOOK_EVENT_NAMES:
        raise ValueError("application event specifications are out of order")
    if tuple(HOOK_INPUT_SCHEMAS) != HOOK_EVENT_NAMES:
        raise ValueError("input schemas are out of order")
    if tuple(HOOK_OUTPUT_SCHEMAS) != HOOK_EVENT_NAMES:
        raise ValueError("output schemas are out of order")

    schema_manifest = manifest.get("schemas")
    if not isinstance(schema_manifest, dict):
        raise ValueError("manifest schemas must be an object")
    input_hash = _schema_sha256({
        event: HOOK_INPUT_SCHEMAS[event]
        for event in HOOK_EVENT_NAMES
    })
    output_hash = _schema_sha256({
        event: HOOK_OUTPUT_SCHEMAS[event]
        for event in HOOK_EVENT_NAMES
    })
    if schema_manifest.get("input_sha256") != input_hash:
        raise ValueError("input schema digest does not match manifest")
    if schema_manifest.get("output_sha256") != output_hash:
        raise ValueError("output schema digest does not match manifest")

    baseline = manifest.get("baseline")
    if not isinstance(baseline, dict):
        raise ValueError("manifest baseline must be an object")
    files = baseline.get("files")
    if not isinstance(files, dict):
        raise ValueError("manifest baseline files must be an object")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("manifest baseline entries must be strings")
        path = ROOT / "codex-main" / relative
        if not path.is_file():
            raise ValueError(f"missing baseline file: {relative}")
        actual = _sha256(path)
        normalized_expected = expected.replace(" ", "").lower()
        if actual != normalized_expected:
            raise ValueError(
                f"baseline file changed: {relative} ({actual} != {normalized_expected})"
            )


def main() -> int:
    """运行清单检查并输出稳定结果。"""
    try:
        check()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"hook alignment check failed: {error}", file=sys.stderr)
        return 1
    print("hook alignment check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
