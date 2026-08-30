# -*- coding: utf-8 -*-

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from infrastructure.platform.sandbox import (
    SandboxClient,
    SandboxProtocolError,
)


def _sidecar_path() -> Path:
    """返回测试使用的 macOS sidecar 路径。"""
    configured = os.environ.get("MIND_SANDBOX_SERVER", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        Path(__file__).resolve().parents[1]
        / "schematic"
        / "sandbox"
        / "macos"
        / "bin"
        / "mind_sandbox_server"
    )


@pytest.fixture
def sidecar_path() -> Path:
    """跳过没有 macOS sidecar 产物的环境。"""
    if sys.platform != "darwin":
        pytest.skip("macOS sidecar integration tests require macOS")
    path = _sidecar_path()
    if not path.is_file() or not os.access(path, os.X_OK):
        pytest.skip(f"macOS sidecar is unavailable: {path}")
    return path


def _new_client(sidecar: Path, workspace: Path) -> SandboxClient:
    """创建指向指定测试工作区的真实 sidecar 客户端。"""
    return SandboxClient(
        workspace_root=workspace,
        application_root=Path(__file__).resolve().parents[1],
        packaged=False,
        platform="darwin",
        executable=sidecar,
    )


async def _run_process(
    client: SandboxClient,
    *,
    argv: tuple[str, ...],
    cwd: Path,
    sandbox_mode: str,
    stdin_open: bool = False,
) -> tuple[bytes, bytes, int]:
    """启动 sidecar 进程并收集完整输出。"""
    process = await client.spawn(
        argv=argv,
        cwd=cwd,
        env={},
        sandbox_mode=sandbox_mode,
        stdin_open=stdin_open,
    )
    stdout_task = asyncio.create_task(process.stdout.read())
    stderr_task = asyncio.create_task(process.stderr.read())
    exit_code = await process.wait()
    stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
    return stdout, stderr, exit_code


def test_sidecar_protocol_and_invalid_requests(sidecar_path: Path, tmp_path: Path) -> None:
    """验证真实 sidecar 的 ready、ping 和 fail-closed 参数校验。"""

    async def run() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        client = _new_client(sidecar_path, workspace)
        try:
            await client.ensure_started()
            result = await client._request("ping", {})
            assert result == {"protocol_version": 1}

            with pytest.raises(SandboxProtocolError, match="argv_empty"):
                await client.spawn(
                    argv=(),
                    cwd=workspace,
                    env={},
                    sandbox_mode="workspace-write",
                    stdin_open=False,
                )

            with pytest.raises(SandboxProtocolError, match="sandbox_mode_disabled"):
                await client.spawn(
                    argv=("/bin/echo", "blocked"),
                    cwd=workspace,
                    env={},
                    sandbox_mode="danger-full-access",
                    stdin_open=False,
                )

            with pytest.raises(SandboxProtocolError, match="cwd_outside_workspace_roots"):
                await client.spawn(
                    argv=("/bin/echo", "blocked"),
                    cwd=workspace.parent,
                    env={},
                    sandbox_mode="read-only",
                    stdin_open=False,
                )
        finally:
            await client.close()

    asyncio.run(run())


@pytest.mark.parametrize("sandbox_mode, expected_success", [
    ("read-only", False),
    ("workspace-read", False),
    ("workspace-write", True),
])
def test_sidecar_filesystem_modes(
    sidecar_path: Path,
    tmp_path: Path,
    sandbox_mode: str,
    expected_success: bool,
) -> None:
    """验证三种受限模式对工作区写入的实际效果。"""

    async def run() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = workspace / "mode-result.txt"
        client = _new_client(sidecar_path, workspace)
        try:
            _, stderr, exit_code = await _run_process(
                client,
                argv=("/bin/sh", "-c", "touch mode-result.txt"),
                cwd=workspace,
                sandbox_mode=sandbox_mode,
            )
            assert target.exists() is expected_success
            assert (exit_code == 0) is expected_success
            if expected_success:
                assert stderr == b""
            else:
                assert b"Operation not permitted" in stderr
        finally:
            await client.close()

    asyncio.run(run())


def test_sidecar_handles_workspace_path_characters(
    sidecar_path: Path,
    tmp_path: Path,
) -> None:
    """验证带空格和非 ASCII 字符的工作区路径可正常传递。"""

    async def run() -> None:
        workspace = tmp_path / "workspace with spaces-工作区"
        workspace.mkdir()
        target = workspace / "path-result.txt"
        client = _new_client(sidecar_path, workspace)
        try:
            _, stderr, exit_code = await _run_process(
                client,
                argv=("/bin/sh", "-c", "touch path-result.txt"),
                cwd=workspace,
                sandbox_mode="workspace-write",
            )
            assert exit_code == 0
            assert stderr == b""
            assert target.exists()
        finally:
            await client.close()

    asyncio.run(run())


def test_sidecar_forwards_large_stdout_and_stderr(
    sidecar_path: Path,
    tmp_path: Path,
) -> None:
    """验证较大的双向输出不会阻塞 sidecar 事件读取。"""

    async def run() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        client = _new_client(sidecar_path, workspace)
        try:
            stdout, stderr, exit_code = await _run_process(
                client,
                argv=(
                    "/bin/sh",
                    "-c",
                    "dd if=/dev/zero bs=1024 count=128 2>/dev/null; "
                    "dd if=/dev/zero bs=1024 count=128 >&2 2>/dev/null",
                ),
                cwd=workspace,
                sandbox_mode="read-only",
            )
            assert exit_code == 0
            assert len(stdout) == 128 * 1024
            assert len(stderr) == 128 * 1024
        finally:
            await client.close()

    asyncio.run(run())


def test_workspace_write_honors_tmp_and_protected_metadata(
    sidecar_path: Path,
    tmp_path: Path,
) -> None:
    """验证 Codex 默认的临时目录写入和元数据目录保护。"""

    async def run() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        for name in (".git", ".agents", ".codex"):
            (workspace / name).mkdir()

        with tempfile.TemporaryDirectory(prefix="mind-sidecar-tmp-") as temp_name:
            temp_target = Path(temp_name) / "created.txt"
            client = _new_client(sidecar_path, workspace)
            try:
                _, _, temp_exit = await _run_process(
                    client,
                    argv=("/bin/sh", "-c", f"touch {temp_target}"),
                    cwd=workspace,
                    sandbox_mode="workspace-write",
                )
                assert temp_exit == 0
                assert temp_target.exists()

                for name in (".git", ".agents", ".codex"):
                    target = workspace / name / "blocked.txt"
                    _, stderr, exit_code = await _run_process(
                        client,
                        argv=("/bin/sh", "-c", f"touch {name}/blocked.txt"),
                        cwd=workspace,
                        sandbox_mode="workspace-write",
                    )
                    assert exit_code != 0
                    assert not target.exists()
                    assert b"Operation not permitted" in stderr
            finally:
                await client.close()

    asyncio.run(run())


def test_workspace_write_rejects_symlink_escape(
    sidecar_path: Path,
) -> None:
    """验证工作区内的 symlink 不能把写入导向工作区外。"""
    with tempfile.TemporaryDirectory(
        prefix="mind-sidecar-symlink-",
        dir=Path.cwd().parent,
    ) as name:
        root = Path(name)
        workspace = root / "workspace"
        outside = root / "outside"
        workspace.mkdir()
        outside.mkdir()
        (workspace / "link").symlink_to(outside, target_is_directory=True)
        target = outside / "escaped.txt"

        async def run() -> None:
            client = _new_client(sidecar_path, workspace)
            try:
                _, stderr, exit_code = await _run_process(
                    client,
                    argv=("/bin/sh", "-c", "touch link/escaped.txt"),
                    cwd=workspace,
                    sandbox_mode="workspace-write",
                )
                assert exit_code != 0
                assert not target.exists()
                assert b"Operation not permitted" in stderr
            finally:
                await client.close()

        asyncio.run(run())


def test_sidecar_stdin_eof_and_terminate(sidecar_path: Path, tmp_path: Path) -> None:
    """验证 stdin/eof 和 terminate 控制请求。"""

    async def run() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        client = _new_client(sidecar_path, workspace)
        try:
            process = await client.spawn(
                argv=("/bin/sh", "-c", "read value; printf 'value=%s' \"$value\""),
                cwd=workspace,
                env={},
                sandbox_mode="read-only",
                stdin_open=True,
            )
            process.stdin.write(b"sidecar-input\n")
            await process.stdin.drain()
            process.stdin.close()
            await process.stdin.wait_closed()
            stdout_task = asyncio.create_task(process.stdout.read())
            stderr_task = asyncio.create_task(process.stderr.read())
            exit_code = await process.wait()
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            assert exit_code == 0
            assert stdout == b"value=sidecar-input"
            assert stderr == b""

            long_running = await client.spawn(
                argv=("/bin/sleep", "30"),
                cwd=workspace,
                env={},
                sandbox_mode="read-only",
                stdin_open=False,
            )
            await client.terminate(long_running.process_id, signal="terminate")
            assert await long_running.wait() != 0
        finally:
            await client.close()

    asyncio.run(run())


def test_sidecar_close_finishes_running_processes(
    sidecar_path: Path,
    tmp_path: Path,
) -> None:
    """验证关闭 sidecar 时不会留下逻辑进程。"""

    async def run() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        client = _new_client(sidecar_path, workspace)
        process = await client.spawn(
            argv=("/bin/sleep", "30"),
            cwd=workspace,
            env={},
            sandbox_mode="read-only",
            stdin_open=False,
        )
        await client.close()
        assert await process.wait() != 0

    asyncio.run(run())


def test_sidecar_artifact_is_macos_arm64_or_universal(sidecar_path: Path) -> None:
    """验证当前开发机上的 sidecar 至少是 macOS Mach-O。"""
    result = subprocess.run(
        ["file", str(sidecar_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    output = result.stdout
    assert "Mach-O" in output
    assert "arm64" in output or "universal" in output
