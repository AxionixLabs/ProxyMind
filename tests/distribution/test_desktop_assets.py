import json
from pathlib import Path

import pytest

from infrastructure.sidecars.assets import (
    desktop_publish_files,
    runtime_asset_files,
)


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    root = tmp_path / "sidecars"
    publish = root / "cua_windows" / "publish"
    publish.mkdir(parents=True)
    for name in ("desktop-sidecar.exe", "desktop-sidecar.dll", "desktop-sidecar.deps.json", "coreclr.dll",
                 "hostfxr.dll", "hostpolicy.dll", "System.Private.CoreLib.dll", "PresentationFramework.dll"):
        (publish / name).write_bytes(b"fixture")
    (publish / "desktop-sidecar.runtimeconfig.json").write_text(json.dumps({"runtimeOptions": {"includedFrameworks": [
        {"name": "Microsoft.NETCore.App"}, {"name": "Microsoft.WindowsDesktop.App"},
    ]}}), encoding="utf-8")
    (root / "js_repl" / "vendor").mkdir(parents=True)
    (root / "js_repl" / "kernel.js").write_text("fixture", encoding="utf-8")
    (root / "js_repl" / "vendor" / "meriyah.umd.min.js").write_text("fixture", encoding="utf-8")
    return root


def test_release_inventory_excludes_sources_caches_and_symbols(assets: Path) -> None:
    (assets / "cua_windows" / "Program.cs").write_text("source", encoding="utf-8")
    (assets / "cua_windows" / "publish" / "desktop-sidecar.pdb").write_bytes(b"symbols")
    (assets / "cua_windows" / "obj").mkdir()
    (assets / "cua_windows" / "obj" / "cache.dll").write_bytes(b"cache")
    files = runtime_asset_files(assets, "win32")
    assert len(files) == 11
    assert not any(path.suffix in {".pdb", ".cs"} or "obj" in path.parts for path in files)
    assert len(runtime_asset_files(assets, "darwin")) == 2


def test_release_fails_when_runtime_is_missing_or_framework_dependent(assets: Path) -> None:
    publish = assets / "cua_windows" / "publish"
    config = publish / "desktop-sidecar.runtimeconfig.json"
    config.write_text('{"runtimeOptions":{"frameworks":[]}}', encoding="utf-8")
    with pytest.raises(ValueError, match="include its .NET"):
        desktop_publish_files(assets)
    (publish / "hostfxr.dll").unlink()
    with pytest.raises(ValueError, match="hostfxr.dll"):
        desktop_publish_files(assets)
