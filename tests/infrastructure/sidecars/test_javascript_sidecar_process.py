from pathlib import Path

import pytest

from infrastructure.sidecars.javascript.bundle import JavaScriptBundle
from infrastructure.sidecars.javascript.process import (
    JavaScriptSidecarProcess,
    parse_node_version,
)
def test_parse_node_version_accepts_supported_version_shape() -> None:
    """Node 版本解析只提取稳定的三段数字。"""
    assert parse_node_version("v24.12.0") == (24, 12, 0)
    assert parse_node_version("22.22.0-nightly") == (22, 22, 0)


@pytest.mark.anyio
async def test_node_arguments_freeze_each_sandbox_mode(
    tmp_path: Path,
    repository_root: Path,
) -> None:
    """Node 权限参数必须由创建 Session 时的安全信封决定。"""
    bundle = JavaScriptBundle.at(repository_root / "sidecars" / "js_repl")
    read_only = JavaScriptSidecarProcess(
        cwd=tmp_path,
        session_id="read-only",
        access_mode="read-only",
        bundle=bundle,
    )
    workspace = JavaScriptSidecarProcess(
        cwd=tmp_path,
        session_id="workspace",
        access_mode="workspace-write",
        bundle=bundle,
    )
    unrestricted = JavaScriptSidecarProcess(
        cwd=tmp_path,
        session_id="unrestricted",
        access_mode="danger-full-access",
        bundle=bundle,
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
