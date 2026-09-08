import asyncio

import pytest

from unittest.mock import AsyncMock

from agent.capabilities import InMemoryProcessCapability
from agent.domain.approvals import NetworkProtocol
from agent.domain.approvals import NetworkTarget
from infrastructure.config.paths import ApplicationLayout
from infrastructure.config.paths import resolve_application_layout
from infrastructure.platform.network import NetworkDecision
from infrastructure.platform.network import StaticNetworkPolicy
from infrastructure.platform.sandbox import (
    SandboxClient,
    SandboxProtocolError,
    SandboxUnavailable,
    SidecarProcess,
    _SidecarStream,
    sandbox_backend_name,
    sandbox_executable_path,
)
from infrastructure.workspace.commands.sandbox_failures import SandboxToolFailure
from infrastructure.workspace.commands.sandbox_failures import map_sandbox_failure
from infrastructure.workspace.runtime import WorkspaceCoding
from infrastructure.platform.process_sessions import (
    ProcessSession,
    ProcessSessionManager,
    ProcessSessionSpec,
)
from mind import create_workspace_coding
from mind import create_workspace_runtime


class _FakeSidecarReader:
    """提供可控 ready 帧和 EOF 的 Sidecar 读取端。"""

    def __init__(self, *lines: bytes, block_after_lines: bool = False) -> None:
        self._lines = list(lines)
        self._block_after_lines = block_after_lines
        self._blocked = asyncio.Event()

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._block_after_lines:
            await self._blocked.wait()
        return b""


class _FakeSidecarWriter:
    """记录客户端写入但不生成响应。"""

    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def write(self, payload: bytes) -> None:
        self.payloads.append(bytes(payload))

    async def drain(self) -> None:
        return None


class _FakeSidecarProcess:
    """模拟 Sidecar 进程的最小异步生命周期。"""

    def __init__(self, stdout: _FakeSidecarReader) -> None:
        self.stdin = _FakeSidecarWriter()
        self.stdout = stdout
        self.stderr = _FakeSidecarReader()
        self.returncode: int | None = None
        self.pid = 4100

    def terminate(self) -> None:
        self.returncode = -15

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


async def _request_with_response(client: SandboxClient, response):
    """向一个已启动的 Fake Sidecar 请求注入指定响应。"""
    process = _FakeSidecarProcess(_FakeSidecarReader(block_after_lines=True))
    client._sidecar = process
    request = asyncio.create_task(client._request("ping", {}))
    await asyncio.sleep(0)
    pending = tuple(client._pending.values())
    assert len(pending) == 1
    pending[0].set_result(response)
    try:
        return await request
    finally:
        process.returncode = -1


def _fake_startable_client(tmp_path) -> SandboxClient:
    """创建显式绑定测试产物路径的 Sandbox 客户端。"""
    executable = tmp_path / "mind_sandbox_server.exe"
    executable.write_bytes(b"test-sidecar")
    return SandboxClient(
        workspace_root=tmp_path,
        executable=executable,
        platform="win32",
    )


@pytest.mark.anyio
async def test_missing_sidecar_fails_before_start(tmp_path) -> None:
    """固定 helper 缺失时的 v1 客户端失败边界。"""
    client = SandboxClient(
        workspace_root=tmp_path,
        executable=tmp_path / "missing-sidecar.exe",
        platform="win32",
    )

    with pytest.raises(SandboxUnavailable, match="sandbox sidecar not found"):
        await client.ensure_started()


@pytest.mark.anyio
async def test_sidecar_start_permission_error_is_not_hidden(
    tmp_path,
    monkeypatch,
) -> None:
    """固定 Sidecar 进程启动拒绝仍保留原始 OS 异常。"""
    client = _fake_startable_client(tmp_path)
    start_sidecar = AsyncMock(
        side_effect=PermissionError(5, "Access is denied"),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start_sidecar)

    with pytest.raises(SandboxUnavailable, match="Access is denied") as raised:
        await client.ensure_started()

    assert raised.value.backend_code == "sidecar_start_failed"
    assert raised.value.stage == "startup"
    assert raised.value.retryable is False


@pytest.mark.anyio
async def test_sidecar_handshake_timeout_has_stable_boundary(
    tmp_path,
    monkeypatch,
) -> None:
    """固定 ready 帧超时对应的可用性失败。"""
    client = _fake_startable_client(tmp_path)
    client.READY_TIMEOUT_SEC = 0.01
    process = _FakeSidecarProcess(
        _FakeSidecarReader(block_after_lines=True),
    )
    start_sidecar = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start_sidecar)

    with pytest.raises(
        SandboxUnavailable,
        match="sandbox sidecar did not become ready",
    ):
        await client.ensure_started()

    ready = client._ready
    assert ready is not None
    assert isinstance(ready.exception(), SandboxUnavailable)


@pytest.mark.anyio
async def test_sidecar_protocol_version_mismatch_is_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    """固定未知 Sidecar 协议版本按 fail-closed 处理。"""
    client = _fake_startable_client(tmp_path)
    process = _FakeSidecarProcess(
        _FakeSidecarReader(b'{"event":"ready","protocol_version":2}\n'),
    )
    start_sidecar = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start_sidecar)

    with pytest.raises(
        SandboxUnavailable,
        match="unsupported sandbox sidecar protocol version: 2",
    ):
        await client.ensure_started()


@pytest.mark.anyio
async def test_sidecar_eof_before_ready_is_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    """固定 ready 前异常 EOF 对应的可用性失败。"""
    client = _fake_startable_client(tmp_path)
    process = _FakeSidecarProcess(_FakeSidecarReader())
    start_sidecar = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start_sidecar)

    with pytest.raises(SandboxUnavailable, match="sandbox sidecar exited"):
        await client.ensure_started()


@pytest.mark.anyio
async def test_sidecar_request_timeout_clears_pending_request(tmp_path) -> None:
    """固定请求超时后 pending future 必须被移除。"""
    client = _fake_startable_client(tmp_path)
    process = _FakeSidecarProcess(
        _FakeSidecarReader(block_after_lines=True),
    )
    client._sidecar = process
    client.REQUEST_TIMEOUT_SEC = 0.01

    with pytest.raises(SandboxProtocolError) as raised:
        await client._request("ping", {})

    assert raised.value.code == "sandbox_protocol_error"
    assert raised.value.backend_code == "sidecar_request_timeout"
    assert raised.value.stage == "request"
    assert raised.value.retryable is True
    assert client._pending == {}
    process.returncode = -1


@pytest.mark.anyio
async def test_sidecar_v1_error_response_preserves_code_and_detail(tmp_path) -> None:
    """验证 v1 错误信封在客户端边界保留独立字段。"""
    client = _fake_startable_client(tmp_path)

    with pytest.raises(SandboxProtocolError) as raised:
        await _request_with_response(client, {
            "ok": False,
            "error": {
                "code": "sandbox_spawn_failed",
                "detail": "CreateProcess failed with OS error 5",
            },
        })

    assert raised.value.code == "sandbox_process_start_failed"
    assert raised.value.backend_code == "sandbox_spawn_failed"
    assert raised.value.detail == "CreateProcess failed with OS error 5"
    assert raised.value.stage == "spawn"
    assert raised.value.retryable is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "expected_detail"),
    (
        ([], "response must be an object"),
        ({"ok": 1, "result": {}}, "field 'ok'"),
        ({"ok": False, "error": "failed"}, "field 'error'"),
        ({"ok": False, "error": {"code": ""}}, "field 'error.code'"),
        (
            {"ok": False, "error": {"code": "failure", "detail": 5}},
            "field 'error.detail'",
        ),
        ({"ok": True, "result": []}, "field 'result'"),
    ),
)
async def test_sidecar_v1_rejects_malformed_response_fields(
    tmp_path,
    response,
    expected_detail: str,
) -> None:
    """验证畸形 v1 响应在协议边界立即失败。"""
    client = _fake_startable_client(tmp_path)

    with pytest.raises(SandboxProtocolError) as raised:
        await _request_with_response(client, response)

    assert raised.value.code == "sandbox_protocol_error"
    assert raised.value.backend_code == "sidecar_response_invalid"
    assert expected_detail in raised.value.detail


def test_source_windows_sidecar_path_is_platform_specific(tmp_path) -> None:
    layout = ApplicationLayout(
        mode="source",
        platform="win32",
        executable=tmp_path / "mind.py",
        root=tmp_path,
        supports=tmp_path / "schematic" / "supports" / "windows",
    )
    expected = (
        tmp_path
        / "schematic"
        / "sandbox"
        / "windows"
        / "bin"
        / "mind_sandbox_server.exe"
    )
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"")

    resolved = SandboxClient(
        workspace_root=tmp_path,
        executable=sandbox_executable_path(layout),
        platform=layout.platform,
    )

    assert resolved.executable == expected.resolve()
    assert resolved.available is True
    assert resolved.platform_name == "windows"


def test_packaged_macos_sidecar_path_is_separate(tmp_path) -> None:
    layout = ApplicationLayout(
        mode="packaged",
        platform="darwin",
        executable=tmp_path / "mind",
        root=tmp_path,
        supports=tmp_path / "schematic" / "supports" / "macos",
    )
    expected = (
        tmp_path
        / "schematic"
        / "sandbox"
        / "macos"
        / "bin"
        / "mind_sandbox_server"
    )
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"")

    client = SandboxClient(
        workspace_root=tmp_path,
        executable=sandbox_executable_path(layout),
        platform=layout.platform,
    )

    assert client.executable == expected.resolve()
    assert client.available is True
    assert sandbox_backend_name("darwin") == "macos-sidecar"


def test_spawn_payload_only_sends_windows_fields_to_windows_sidecar(
    tmp_path, monkeypatch
) -> None:
    captured: dict[str, dict[str, object]] = {}

    async def run(platform: str) -> None:
        client = SandboxClient(
            workspace_root=tmp_path,
            executable=tmp_path / "mind_sandbox_server",
            platform=platform,
        )

        async def fake_ensure_started() -> None:
            return None

        async def fake_request(method: str, params: dict[str, object]) -> dict[str, str]:
            captured[platform] = dict(params)
            return {"process_id": f"sandbox-{platform}"}

        monkeypatch.setattr(client, "ensure_started", fake_ensure_started)
        monkeypatch.setattr(client, "_request", fake_request)
        await client.spawn(
            argv=("echo", "ok"),
            cwd=tmp_path,
            env={},
            sandbox_mode="workspace-write",
            stdin_open=False,
            additional_permissions={
                "file_system": {"read": [str(tmp_path / "out.txt")]},
            },
        )

    asyncio.run(run("darwin"))
    asyncio.run(run("linux"))
    asyncio.run(run("win32"))

    assert "level" not in captured["darwin"]
    assert "level" not in captured["linux"]
    assert captured["win32"]["level"] == "restricted-token"
    assert captured["win32"]["additional_permissions"]["file_system"]["read"] == [
        str(tmp_path / "out.txt")
    ]


def test_native_coding_reuses_application_layout_for_sandbox_paths(tmp_path) -> None:
    layout = ApplicationLayout(
        mode="packaged",
        platform="darwin",
        executable=tmp_path / "Mind.app" / "Contents" / "MacOS" / "mind",
        root=tmp_path / "Mind.app" / "Contents" / "MacOS",
        supports=(
            tmp_path
            / "Mind.app"
            / "Contents"
            / "MacOS"
            / "schematic"
            / "supports"
            / "macos"
        ),
    )
    coding = create_workspace_coding(
        root=tmp_path / "workspace",
        application_layout=layout,
    )

    try:
        sandbox_client = coding._process_sessions._sandbox_client
        assert sandbox_client is not None
        assert sandbox_client.executable == sandbox_executable_path(layout)
        assert sandbox_client.platform == layout.platform
    finally:
        asyncio.run(coding.close())


def test_workspace_runtime_hydrates_persistent_network_rules(tmp_path) -> None:
    rules = tmp_path / ".mind" / "rules"
    rules.mkdir(parents=True)
    (rules / "local.rules").write_text(
        'network_rule(host="api.example.com", protocol="https", decision="allow")\n',
        encoding="utf-8",
    )
    policy = StaticNetworkPolicy()
    runtime = create_workspace_runtime(
        tmp_path,
        network_policy=policy,
    )
    try:
        assert policy.decide(NetworkTarget(
            "api.example.com",
            NetworkProtocol.HTTPS,
            443,
        )) is NetworkDecision.ALLOW
    finally:
        asyncio.run(runtime.close())


def test_network_enabled_does_not_create_managed_proxy(tmp_path) -> None:
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        network_access="enabled",
    )
    try:
        assert coding._process_sessions._network_proxy is None
    finally:
        asyncio.run(coding.close())


def test_composition_resolves_source_sandbox_layout_when_omitted(
    tmp_path,
    repository_root,
) -> None:
    """验证组合根为测试调用补齐源码布局而非由客户端猜测层级。"""
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=None,
        network_access="enabled",
    )
    layout = resolve_application_layout(
        entry_file=repository_root / "mind.py",
        argv0="mind.py",
    )
    expected = sandbox_executable_path(layout)
    try:
        client = coding._process_sessions._sandbox_client
        assert client is not None
        assert client.executable == expected
    finally:
        asyncio.run(coding.close())


def test_sidecar_stream_read_without_size_collects_until_eof() -> None:
    """验证无 size 的读取会合并全部事件块并等待 EOF。"""

    async def run() -> None:
        stream = _SidecarStream()
        stream.feed(b"first")
        stream.feed(b"second")
        stream.close()

        assert await stream.read() == b"firstsecond"

    asyncio.run(run())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("platform", "expected_eof"),
    (
        ("win32", b"\x1a\r"),
        ("darwin", b"\x04"),
        ("linux", b"\x04"),
    ),
)
async def test_sidecar_pty_controls_use_terminal_input(
    tmp_path,
    monkeypatch,
    platform: str,
    expected_eof: bytes,
) -> None:
    client = SandboxClient(
        workspace_root=tmp_path,
        executable=tmp_path / "mind_sandbox_server",
        platform=platform,
    )
    writes: list[tuple[str, bytes, bool]] = []

    async def capture_write(
        process_id: str,
        *,
        data: bytes = b"",
        eof: bool = False,
    ) -> None:
        writes.append((process_id, data, eof))

    monkeypatch.setattr(client, "write", capture_write)

    await client.interrupt("sandbox-pty", tty=True)
    await client.close_input("sandbox-pty", tty=True)

    assert writes == [
        ("sandbox-pty", b"\x03", False),
        ("sandbox-pty", expected_eof, False),
    ]


@pytest.mark.anyio
async def test_sidecar_pipe_controls_use_process_protocol(
    tmp_path,
    monkeypatch,
) -> None:
    client = SandboxClient(
        workspace_root=tmp_path,
        executable=tmp_path / "mind_sandbox_server.exe",
        platform="win32",
    )
    terminations: list[tuple[str, str]] = []
    writes: list[tuple[str, bytes, bool]] = []

    async def capture_terminate(
        process_id: str,
        *,
        signal: str = "terminate",
    ) -> None:
        terminations.append((process_id, signal))

    async def capture_write(
        process_id: str,
        *,
        data: bytes = b"",
        eof: bool = False,
    ) -> None:
        writes.append((process_id, data, eof))

    monkeypatch.setattr(client, "terminate", capture_terminate)
    monkeypatch.setattr(client, "write", capture_write)

    await client.interrupt("sandbox-pipe", tty=False)
    await client.close_input("sandbox-pipe", tty=False)

    assert terminations == [("sandbox-pipe", "interrupt")]
    assert writes == [("sandbox-pipe", b"", True)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("control", "expected_reason"),
    (
        ("interrupt", "exec_interrupt_failed"),
        ("eof", "exec_stdin_closed"),
        ("terminate", "exec_terminate_failed"),
    ),
)
async def test_sidecar_control_failures_return_stable_tool_reasons(
    tmp_path,
    monkeypatch,
    control: str,
    expected_reason: str,
) -> None:
    client = SandboxClient(
        workspace_root=tmp_path,
        executable=tmp_path / "mind_sandbox_server.exe",
        platform="win32",
    )

    async def fail_tty_control(process_id: str, *, tty: bool) -> None:
        del process_id, tty
        raise SandboxProtocolError("synthetic failure")

    async def fail_terminate(process_id: str, *, signal: str) -> None:
        del process_id, signal
        raise SandboxProtocolError("synthetic failure")

    monkeypatch.setattr(client, "interrupt", fail_tty_control)
    monkeypatch.setattr(client, "close_input", fail_tty_control)
    monkeypatch.setattr(client, "terminate", fail_terminate)
    process = SidecarProcess(client, "sandbox-failure")
    session = ProcessSession(
        session_id="exec_failure",
        spec=ProcessSessionSpec(
            command="failure",
            args=("failure",),
            cwd=str(tmp_path),
            display_cwd=str(tmp_path),
            runtime={},
            origin="test",
            timeout_sec=30,
            idle_timeout_sec=30,
            tty=True,
        ),
        process=process,
    )
    manager = ProcessSessionManager(sandbox_client=client)

    manager.sessions[session.session_id] = session
    coding = WorkspaceCoding(root=tmp_path, process_sessions=manager)
    try:
        result = await coding.write_stdin(
            session_id=session.session_id,
            control=control,
        )
    finally:
        manager.remove(session.session_id)
        process.finish(-1)
        await coding.close()

    data = result["data"]
    failure = map_sandbox_failure(
        SandboxProtocolError("synthetic failure"),
        control=control,
    )
    assert isinstance(failure, SandboxToolFailure)
    assert data["reason"] == failure.reason == expected_reason
    assert data["backend_code"] == failure.backend_code == "synthetic failure"
    assert data["stage"] == failure.stage == "control"


@pytest.mark.anyio
async def test_process_session_manager_owns_byte_output_snapshot(tmp_path) -> None:
    """验证字节输出、顺序记录和截断事实由会话管理器投影。"""
    client = SandboxClient(
        workspace_root=tmp_path,
        executable=tmp_path / "mind_sandbox_server.exe",
        platform="win32",
    )
    process = SidecarProcess(client, "sandbox-output")
    session = ProcessSession(
        session_id="exec_output",
        spec=ProcessSessionSpec(
            command="output",
            args=("output",),
            cwd=str(tmp_path),
            display_cwd=str(tmp_path),
            runtime={},
            origin="test",
            timeout_sec=30,
            idle_timeout_sec=30,
        ),
        process=process,
    )
    manager = ProcessSessionManager(sandbox_client=client)
    encoded = "中文\n".encode("utf-8")

    await manager._record_output(session, "stdout", encoded[:2])
    await manager._record_output(session, "stdout", encoded[2:])
    await manager._record_output(session, "stderr", b"warning\n")
    await manager._finish_output_stream(session, "stdout")
    await manager._finish_output_stream(session, "stderr")

    snapshot = await manager.byte_output_snapshot(session)

    assert snapshot.stdout == encoded
    assert snapshot.stderr == b"warning\n"
    assert snapshot.stdout_dropped == 0
    assert snapshot.stderr_dropped == 0
    assert [
        (record.stream, record.data)
        for record in snapshot.output_records
    ] == [
        ("stdout", "中文".encode("utf-8")),
        ("stderr", b"warning"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ("shell_command", "exec_command"))
async def test_sandbox_spawn_failure_uses_shared_tool_mapping(
    tmp_path,
    monkeypatch,
    tool: str,
) -> None:
    """验证两个命令入口共享 v1 失败映射和 failure_context。"""
    layout = ApplicationLayout(
        mode="source",
        platform="win32",
        executable=tmp_path / "mind.py",
        root=tmp_path,
        supports=tmp_path / "schematic" / "supports" / "windows",
    )
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=layout,
        network_access="enabled",
    )
    client = coding._process_sessions._sandbox_client
    assert client is not None

    async def fail_spawn(**_kwargs):
        raise SandboxProtocolError(
            "sandbox_spawn_failed",
            "CreateProcess failed with OS error 5",
        )

    monkeypatch.setattr(client, "spawn", fail_spawn)
    try:
        execute = getattr(coding, tool)
        result = await execute(
            command="Write-Output ok",
            sandbox_mode="workspace-read",
        )
    finally:
        await coding.close()

    data = result["data"]
    assert result["ok"] is False
    assert data["reason"] == "sandbox_process_start_failed"
    assert data["backend_code"] == "sandbox_spawn_failed"
    assert data["stage"] == "spawn"
    assert data["retryable"] is False
    assert data["detail"] == "CreateProcess failed with OS error 5"
    assert data["failure_context"]["backend_code"] == "sandbox_spawn_failed"


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ("shell_command", "exec_command"))
async def test_unknown_execution_error_is_not_sandbox_unavailable(
    tmp_path,
    monkeypatch,
    tool: str,
) -> None:
    """验证未知实现异常按工具内部错误观测。"""
    layout = ApplicationLayout(
        mode="source",
        platform="win32",
        executable=tmp_path / "mind.py",
        root=tmp_path,
        supports=tmp_path / "schematic" / "supports" / "windows",
    )
    coding = create_workspace_coding(
        root=tmp_path,
        application_layout=layout,
        network_access="enabled",
    )
    client = coding._process_sessions._sandbox_client
    assert client is not None

    async def fail_spawn(**_kwargs):
        raise RuntimeError("synthetic implementation defect")

    monkeypatch.setattr(client, "spawn", fail_spawn)
    try:
        execute = getattr(coding, tool)
        result = await execute(
            command="Write-Output ok",
            sandbox_mode="workspace-read",
        )
    finally:
        await coding.close()

    data = result["data"]
    assert result["ok"] is False
    assert data["reason"] == "tool_internal_error"
    assert data["exception_type"] == "RuntimeError"
    assert "backend_code" not in data


@pytest.mark.anyio
async def test_process_session_manager_uses_injected_process_capability() -> None:
    """验证完整权限进程通过 capability 端口完成输出和回收。"""
    capability = InMemoryProcessCapability(
        stdout="capability output",
        stderr="capability warning",
    )
    manager = ProcessSessionManager(process_capability=capability)
    spec = ProcessSessionSpec(
        command="echo",
        args=("echo", "capability"),
        cwd=".",
        display_cwd=".",
        runtime={},
        origin="test",
        timeout_sec=30,
        idle_timeout_sec=30,
    )

    session = await manager.start(spec)
    await session.process.wait()
    snapshot = await manager.output_snapshot(
        session.session_id,
        max_output_chars=12000,
    )

    assert snapshot["status"] == "exited"
    assert snapshot["stdout"] == "capability output"
    assert snapshot["stderr"] == "capability warning"

    await manager.close()
    await capability.aclose()
