from pathlib import Path

import pytest

from infrastructure.sidecars.javascript.bundle import JavaScriptBundle
from infrastructure.sidecars.javascript.process import (
    JavaScriptSidecarProcess,
    parse_node_version,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLE = JavaScriptBundle.at(PROJECT_ROOT / "sidecars" / "js_repl")


def test_parse_node_version_accepts_supported_version_shape() -> None:
    """Node 版本解析只提取稳定的三段数字。"""
    assert parse_node_version("v24.12.0") == (24, 12, 0)
    assert parse_node_version("22.22.0-nightly") == (22, 22, 0)


@pytest.mark.anyio
async def test_node_arguments_freeze_each_sandbox_mode(tmp_path: Path) -> None:
    """Node 权限参数必须由创建 Session 时的安全信封决定。"""
    read_only = JavaScriptSidecarProcess(
        cwd=tmp_path,
        session_id="read-only",
        access_mode="read-only",
        bundle=BUNDLE,
    )
    workspace = JavaScriptSidecarProcess(
        cwd=tmp_path,
        session_id="workspace",
        access_mode="workspace-write",
        bundle=BUNDLE,
    )
    unrestricted = JavaScriptSidecarProcess(
        cwd=tmp_path,
        session_id="unrestricted",
        access_mode="danger-full-access",
        bundle=BUNDLE,
    )
    try:
        assert "--permission" in read_only.node_arguments()
        assert f"--allow-fs-write={tmp_path.resolve()}" not in (
            read_only.node_arguments()
        )
        assert f"--allow-fs-write={tmp_path.resolve()}" in (
            workspace.node_arguments()
        )
        assert unrestricted.node_arguments() == ("--experimental-vm-modules",)
    finally:
        await read_only.close()
        await workspace.close()
        await unrestricted.close()
