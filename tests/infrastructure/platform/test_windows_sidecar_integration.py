import asyncio
import os
from pathlib import Path

import pytest

from infrastructure.config.paths import ApplicationLayout
from infrastructure.config.paths import resolve_application_layout
from infrastructure.platform.sandbox import SandboxClient
from infrastructure.platform.sandbox import SandboxProtocolError
from infrastructure.platform.shell_runtime import ShellRuntimeResolver
from mind import create_workspace_coding


pytestmark = pytest.mark.runtime_p0


def _windows_sandbox_layout_or_skip(repository_root: Path) -> ApplicationLayout:
    """返回绑定真实 Windows Sidecar 产物的应用布局。"""
    if os.name != "nt":
        pytest.skip("Windows Sidecar integration requires Windows")
    layout = resolve_application_layout(
        entry_file=repository_root / "mind.py",
        argv0="mind.py",
        platform="win32",
    )
    sidecar = (
        layout.root
        / "schematic"
        / "sandbox"
        / "windows"
        / "bin"
        / "mind_sandbox_server.exe"
    )
    if not sidecar.is_file():
        pytest.skip(f"Windows Sidecar is unavailable: {sidecar}")
    return layout


def _new_windows_client(
    workspace: Path,
    repository_root: Path,
) -> SandboxClient:
    """创建显式绑定应用布局的真实 Windows Sidecar 客户端。"""
    layout = _windows_sandbox_layout_or_skip(repository_root)
    return SandboxClient(
        workspace_root=workspace,
        application_root=layout.root,
        packaged=layout.packaged,
        platform=layout.platform,
    )


async def _run_process(
    client: SandboxClient,
    *,
    command: str,
    cwd: Path,
    timeout_ms: int = 10000,
) -> tuple[bytes, bytes, int]:
    """通过真实 Sidecar 执行 PowerShell 并完整收束双流。"""
    env = os.environ.copy()
    runtime = ShellRuntimeResolver.resolve(env=env)
    process = await client.spawn(
        argv=(*runtime.prefix, command),
        cwd=cwd,
        env=env,
        sandbox_mode="workspace-read",
        stdin_open=False,
        timeout_ms=timeout_ms,
    )
    stdout_task = asyncio.create_task(process.stdout.read())
    stderr_task = asyncio.create_task(process.stderr.read())
    exit_code = await asyncio.wait_for(process.wait(), timeout=15)
    stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
    return stdout, stderr, exit_code


@pytest.mark.anyio
async def test_windows_sidecar_v1_non_tty_process_baselines(
    tmp_path: Path,
    repository_root: Path,
) -> None:
    """固定真实 Sidecar 的读取、失败、解析和大输出事实。"""
    target = tmp_path / "baseline.txt"
    target.write_text("sandbox baseline", encoding="utf-8")
    escaped_target = str(target).replace("'", "''")
    client = _new_windows_client(tmp_path, repository_root)

    try:
        stdout, stderr, exit_code = await _run_process(
            client,
            command=f"Get-Content -LiteralPath '{escaped_target}'",
            cwd=tmp_path,
        )
        assert exit_code == 0
        assert stdout.decode("utf-8").strip() == "sandbox baseline"
        assert stderr == b""

        stdout, stderr, exit_code = await _run_process(
            client,
            command="exit 7",
            cwd=tmp_path,
        )
        assert exit_code == 7
        assert stdout == b""
        assert stderr == b""

        stdout, stderr, exit_code = await _run_process(
            client,
            command="python - <<'PY'",
            cwd=tmp_path,
        )
        assert exit_code == 1
        assert stdout == b""
        assert b"ParserError" in stderr

        stdout, stderr, exit_code = await _run_process(
            client,
            command="Write-Output ('x' * 30000)",
            cwd=tmp_path,
        )
        assert exit_code == 0
        assert len(stdout) >= 30000
        assert stderr == b""
    finally:
        await client.close()


@pytest.mark.anyio
async def test_windows_sidecar_v1_invalid_request_codes(
    tmp_path: Path,
    repository_root: Path,
) -> None:
    """固定 v1 Sidecar 已声明的请求和启动错误码。"""
    client = _new_windows_client(tmp_path, repository_root)
    common = {
        "env": os.environ.copy(),
        "stdin_open": False,
        "timeout_ms": 5000,
    }

    try:
        with pytest.raises(SandboxProtocolError, match="^sandbox_spawn_failed$"):
            await client.spawn(
                argv=(str(tmp_path / "missing-command.exe"),),
                cwd=tmp_path,
                sandbox_mode="workspace-read",
                **common,
            )

        with pytest.raises(
            SandboxProtocolError,
            match="^cwd_outside_workspace_roots$",
        ):
            await client.spawn(
                argv=("cmd.exe", "/d", "/c", "exit 0"),
                cwd=tmp_path.parent,
                sandbox_mode="workspace-read",
                **common,
            )

        with pytest.raises(
            SandboxProtocolError,
            match="^sandbox_mode_disabled$",
        ):
            await client.spawn(
                argv=("cmd.exe", "/d", "/c", "exit 0"),
                cwd=tmp_path,
                sandbox_mode="danger-full-access",
                **common,
            )

        with pytest.raises(SandboxProtocolError, match="^argv_empty$"):
            await client.spawn(
                argv=(),
                cwd=tmp_path,
                sandbox_mode="workspace-read",
                **common,
            )
    finally:
        await client.close()


@pytest.mark.xfail(
    strict=True,
    raises=AttributeError,
    reason="阶段 1 将 v1 code/detail 转换为结构化失败属性",
)
@pytest.mark.parametrize(
    (
        "backend_code",
        "reason",
        "stage",
        "retryable",
    ),
    (
        (
            "sandbox_spawn_failed",
            "sandbox_process_start_failed",
            "spawn",
            False,
        ),
        (
            "cwd_outside_workspace_roots",
            "sandbox_request_invalid",
            "request",
            False,
        ),
        (
            "sandbox_mode_disabled",
            "sandbox_request_invalid",
            "request",
            False,
        ),
        (
            "argv_empty",
            "sandbox_request_invalid",
            "request",
            False,
        ),
    ),
)
def test_v1_protocol_failure_exposes_stable_mapping_facts(
    backend_code: str,
    reason: str,
    stage: str,
    retryable: bool,
) -> None:
    """冻结阶段 1 必须实现的 v1 错误保真契约。"""
    failure = SandboxProtocolError(backend_code)

    assert failure.code == reason
    assert failure.backend_code == backend_code
    assert failure.stage == stage
    assert failure.retryable is retryable


@pytest.mark.xfail(
    strict=True,
    raises=TypeError,
    reason="阶段 1 将 shell_command 切换到 ProcessSessionManager 输出快照",
)
@pytest.mark.anyio
@pytest.mark.parametrize(
    "scenario",
    (
        "success",
        "nonzero",
        "parser_error",
        "timeout",
        "truncation",
    ),
)
async def test_windows_sandboxed_shell_command_non_tty_contract(
    tmp_path: Path,
    repository_root: Path,
    scenario: str,
) -> None:
    """冻结一次性 Sandbox 命令在阶段 1 修复后的公开结果契约。"""
    target = tmp_path / "shell-command.txt"
    target.write_text("shell command baseline", encoding="utf-8")
    escaped_target = str(target).replace("'", "''")
    commands = {
        "success": f"Get-Content -LiteralPath '{escaped_target}'",
        "nonzero": "exit 7",
        "parser_error": "python - <<'PY'",
        "timeout": "Start-Sleep -Seconds 5",
        "truncation": "Write-Output ('x' * 30000)",
    }
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=_windows_sandbox_layout_or_skip(repository_root),
        network_access="enabled",
    )

    try:
        result = await coding.shell_command(
            command=commands[scenario],
            sandbox_mode="workspace-read",
            timeout_sec=1 if scenario == "timeout" else 10,
        )
    finally:
        await coding.close()

    data = result["data"]
    assert data["execution_backend"] == "windows-sidecar"
    if scenario == "success":
        assert result["ok"] is True
        assert data["stdout"].strip() == "shell command baseline"
    elif scenario == "nonzero":
        assert result["ok"] is False
        assert data["reason"] == "command_failed"
        assert data["exit_code"] == 7
    elif scenario == "parser_error":
        assert result["ok"] is False
        assert data["reason"] == "command_failed"
        assert data["exit_code"] == 1
        assert "ParserError" in data["stderr"]
    elif scenario == "timeout":
        assert result["ok"] is False
        assert data["reason"] == "command_timed_out"
        assert data["timed_out"] is True
    else:
        assert result["ok"] is True
        assert data["stdout_truncated"] is True
        assert data["truncated"] is True
