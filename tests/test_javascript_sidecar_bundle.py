import shutil
from pathlib import Path

import pytest

from agent.ports.javascript import (
    JavaScriptExecutionError,
    JavaScriptFailureKind,
)
from infrastructure.sidecars.javascript.bundle import (
    KERNEL_SHA256,
    PARSER_SHA256,
    JavaScriptBundle,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "sidecars" / "js_repl"


def test_repository_javascript_bundle_matches_immutable_manifest() -> None:
    """仓库中的 Codex JavaScript bundle 必须与固定清单完全一致。"""
    bundle = JavaScriptBundle.at(ASSET_ROOT)

    bundle.verify()

    assert bundle.kernel_path.name == "kernel.js"
    assert bundle.parser_path.relative_to(bundle.root).as_posix() == (
        "vendor/meriyah.umd.min.js"
    )
    assert len(KERNEL_SHA256) == 64
    assert len(PARSER_SHA256) == 64


def test_javascript_bundle_rejects_missing_or_changed_asset(tmp_path: Path) -> None:
    """缺失或被改写的 Codex 资产必须在启动进程前失败。"""
    target = tmp_path / "sidecars" / "js_repl"
    (target / "vendor").mkdir(parents=True)
    shutil.copyfile(ASSET_ROOT / "kernel.js", target / "kernel.js")
    bundle = JavaScriptBundle.at(target)

    with pytest.raises(
        JavaScriptExecutionError,
        match="asset is missing: vendor/meriyah.umd.min.js",
    ) as missing:
        bundle.verify()
    assert missing.value.kind == JavaScriptFailureKind.UNAVAILABLE

    shutil.copyfile(
        ASSET_ROOT / "vendor" / "meriyah.umd.min.js",
        target / "vendor" / "meriyah.umd.min.js",
    )
    (target / "kernel.js").write_bytes(b"changed")

    with pytest.raises(
        JavaScriptExecutionError,
        match="asset integrity check failed: kernel.js",
    ) as changed:
        bundle.verify()
    assert changed.value.kind == JavaScriptFailureKind.UNAVAILABLE
