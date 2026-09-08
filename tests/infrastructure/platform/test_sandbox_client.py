import asyncio
import base64
import ctypes
import ctypes.wintypes
import gc
import json
import os
import tracemalloc

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
    SandboxOutcomeUnknown,
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

    async def read(self, size: int = -1) -> bytes:
        del size
        return await self.readline()


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

    def __init__(
        self,
        stdout: _FakeSidecarReader,
        *,
        stderr: _FakeSidecarReader | None = None,
    ) -> None:
        self.stdin = _FakeSidecarWriter()
        self.stdout = stdout
        self.stderr = stderr or _FakeSidecarReader()
        self.returncode: int | None = None
        self.pid = 4100

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _bind_request_transport(
    client: SandboxClient,
    process: _FakeSidecarProcess,
    *,
    generation: int = 1,
) -> None:
    """把请求测试绑定到一个已完成握手的 Fake Sidecar 代次。"""
    async def already_started() -> None:
        return None

    client._sidecar = process
    client._generation = generation
    client._sidecar_generation = generation
    client._transport_failed = False
    client.ensure_started = already_started


async def _request_with_response(client: SandboxClient, response):
    """向一个已启动的 Fake Sidecar 请求注入指定响应。"""
    process = _FakeSidecarProcess(_FakeSidecarReader(block_after_lines=True))
    _bind_request_transport(client, process)
    request = asyncio.create_task(client._request("ping", {}))
    await asyncio.sleep(0)
    pending = tuple(client._pending.values())
    assert len(pending) == 1
    pending[0].set_result(response)
    try:
        return await request
    finally:
        process.returncode = -1


def _jsonl_frame(payload) -> bytes:
    """编码一个测试用 JSONL 帧。"""
    return (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")


def _bind_reader_transport(
    client: SandboxClient,
    process: _FakeSidecarProcess,
    *,
    generation: int = 1,
) -> asyncio.Future[bool]:
    """把协议读取测试绑定到指定 Fake Sidecar 代次。"""
    ready = asyncio.get_running_loop().create_future()
    ready.add_done_callback(client._consume_future_exception)
    client._sidecar = process
    client._generation = generation
    client._sidecar_generation = generation
    client._transport_failed = False
    client._ready = ready
    return ready


def _process_handle_count() -> int | None:
    """返回 Windows 当前进程句柄数，其他平台不提供该指标。"""
    if os.name != "nt":
        return None
    count = ctypes.wintypes.DWORD()
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = (
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    )
    kernel32.GetProcessHandleCount.restype = ctypes.wintypes.BOOL
    succeeded = kernel32.GetProcessHandleCount(
        kernel32.GetCurrentProcess(),
        ctypes.byref(count),
    )
    if not succeeded:
        raise ctypes.WinError()
    return int(count.value)


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
    _bind_request_transport(client, process)
    client.REQUEST_TIMEOUT_SEC = 0.01

    with pytest.raises(SandboxProtocolError) as raised:
        await client._request("ping", {})

    assert raised.value.code == "sandbox_protocol_error"
    assert raised.value.backend_code == "sidecar_request_timeout"
    assert raised.value.stage == "request"
    assert raised.value.retryable is True
    assert client._pending == {}
    assert json.loads(process.stdin.payloads[0])["id"] == "g1:r1"
    process.returncode = -1


@pytest.mark.anyio
async def test_effectful_request_timeout_is_unknown_and_stops_generation(
    tmp_path,
) -> None:
    """验证可能已生效的超时请求不可重试且会收束当前代次。"""
    client = _fake_startable_client(tmp_path)
    client.REQUEST_TIMEOUT_SEC = 0.01
    process = _FakeSidecarProcess(
        _FakeSidecarReader(block_after_lines=True),
    )
    _bind_request_transport(client, process)

    with pytest.raises(SandboxOutcomeUnknown) as raised:
        await client._request("spawn", {"argv": ["side-effect"]})

    assert raised.value.backend_code == "sidecar_request_timeout"
    assert raised.value.stage == "spawn"
    assert raised.value.retryable is False
    assert client._pending == {}
    assert client._sidecar is None
    assert process.returncode == -15


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
            client._sidecar_generation = 1
            return None

        async def fake_request(
            method: str,
            params,
            *,
            generation: int | None = None,
        ) -> dict[str, str]:
            assert generation == 1
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
        generation: int | None = None,
    ) -> None:
        del generation
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
        generation: int | None = None,
    ) -> None:
        del generation
        terminations.append((process_id, signal))

    async def capture_write(
        process_id: str,
        *,
        data: bytes = b"",
        eof: bool = False,
        generation: int | None = None,
    ) -> None:
        del generation
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

    async def fail_tty_control(
        process_id: str,
        *,
        tty: bool,
        generation: int | None = None,
    ) -> None:
        del process_id, tty, generation
        raise SandboxProtocolError("synthetic failure")

    async def fail_terminate(
        process_id: str,
        *,
        signal: str,
        generation: int | None = None,
    ) -> None:
        del process_id, signal, generation
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


def test_unknown_control_outcome_is_not_downgraded_to_control_failure() -> None:
    """验证控制请求失联时保留结果未知语义和不可重试事实。"""
    failure = map_sandbox_failure(
        SandboxOutcomeUnknown("sidecar exited during terminate"),
        control="terminate",
    )

    assert failure.reason == "execution_outcome_unknown"
    assert failure.stage == "control"
    assert failure.retryable is False


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


@pytest.mark.anyio
async def test_sidecar_stderr_tail_is_bounded_and_attached_to_startup_failure(
    tmp_path,
    monkeypatch,
) -> None:
    """验证 ready 前退出会持续消费 stderr 且只保留有限尾部。"""
    client = _fake_startable_client(tmp_path)
    client.STDERR_TAIL_LIMIT_BYTES = 32
    process = _FakeSidecarProcess(
        _FakeSidecarReader(),
        stderr=_FakeSidecarReader(b"a" * 64, b"b" * 64),
    )
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )

    with pytest.raises(SandboxUnavailable) as raised:
        await client.ensure_started()

    assert bytes(client._stderr_tail) == b"b" * 32
    assert "b" * 32 in raised.value.detail
    assert "a" * 16 not in raised.value.detail
    assert client._reader_task is None
    assert client._stderr_task is None
    ready = client._ready
    assert ready is not None
    assert ready.done()
    assert ready._log_traceback is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("frame", "backend_code"),
    (
        (b"\xff\n", "sidecar_frame_invalid_utf8"),
        (b"{invalid}\n", "sidecar_frame_invalid_json"),
        (b"[]\n", "sidecar_frame_invalid"),
        (_jsonl_frame({"event": "mystery"}), "sidecar_event_unknown"),
        (
            _jsonl_frame({"event": "stdout", "process_id": "", "data": ""}),
            "sidecar_event_invalid",
        ),
        (
            _jsonl_frame({
                "event": "stderr",
                "process_id": "sandbox-1",
                "data": "not base64!",
            }),
            "sidecar_event_invalid",
        ),
        (
            _jsonl_frame({"event": "exit", "process_id": "sandbox-1"}),
            "sidecar_event_invalid",
        ),
        (
            _jsonl_frame({"event": 7, "id": "g1:r1"}),
            "sidecar_event_invalid",
        ),
        (_jsonl_frame({}), "sidecar_response_invalid"),
    ),
)
async def test_sidecar_malformed_frames_fail_closed(
    tmp_path,
    frame: bytes,
    backend_code: str,
) -> None:
    """验证畸形帧不会被静默忽略或切换到其他执行后端。"""
    client = _fake_startable_client(tmp_path)
    process = _FakeSidecarProcess(_FakeSidecarReader(frame))
    ready = _bind_reader_transport(client, process)

    await client._read_events(process, 1, bytearray())

    failure = client._transport_error
    assert isinstance(failure, SandboxProtocolError)
    assert failure.backend_code == backend_code
    assert failure.retryable is False
    assert ready.exception() is failure
    assert client.generation == 1


@pytest.mark.anyio
async def test_sidecar_oversized_and_duplicate_frames_are_protocol_failures(
    tmp_path,
) -> None:
    """验证单帧上限和重复 ready 均关闭当前协议代次。"""
    oversized_client = _fake_startable_client(tmp_path)
    oversized_client.FRAME_LIMIT_BYTES = 32
    oversized_process = _FakeSidecarProcess(
        _FakeSidecarReader(b"{" + b"x" * 40 + b"}\n"),
    )
    _bind_reader_transport(oversized_client, oversized_process)

    await oversized_client._read_events(
        oversized_process,
        1,
        bytearray(),
    )

    oversized_failure = oversized_client._transport_error
    assert isinstance(oversized_failure, SandboxProtocolError)
    assert oversized_failure.backend_code == "sidecar_frame_too_large"

    duplicate_client = _fake_startable_client(tmp_path)
    ready_frame = _jsonl_frame({"event": "ready", "protocol_version": 1})
    duplicate_process = _FakeSidecarProcess(
        _FakeSidecarReader(ready_frame, ready_frame),
    )
    ready = _bind_reader_transport(duplicate_client, duplicate_process)

    await duplicate_client._read_events(
        duplicate_process,
        1,
        bytearray(),
    )

    duplicate_failure = duplicate_client._transport_error
    assert ready.result() is True
    assert isinstance(duplicate_failure, SandboxProtocolError)
    assert duplicate_failure.backend_code == "sidecar_event_duplicate"


@pytest.mark.anyio
async def test_sidecar_replays_early_events_and_releases_exited_process(
    tmp_path,
) -> None:
    """验证乱序输出和退出可重放，退出后立即释放进程表。"""
    client = _fake_startable_client(tmp_path)
    fake_sidecar = _FakeSidecarProcess(
        _FakeSidecarReader(block_after_lines=True),
    )
    _bind_request_transport(client, fake_sidecar)
    output_event = client._decode_process_event({
        "event": "stdout",
        "process_id": "sandbox-early",
        "data": base64.b64encode(b"early output").decode("ascii"),
    }, "stdout")
    exit_event = client._decode_process_event({
        "event": "exit",
        "process_id": "sandbox-early",
        "exit_code": 7,
    }, "exit")
    client._handle_process_event(output_event, generation=1)
    client._handle_process_event(exit_event, generation=1)

    async def spawn_response(
        method: str,
        params,
        *,
        generation: int | None = None,
    ) -> dict[str, str]:
        del params
        assert method == "spawn"
        assert generation == 1
        return {"process_id": "sandbox-early"}

    client._request = spawn_response
    process = await client.spawn(
        argv=("echo", "early"),
        cwd=tmp_path,
        env={},
        sandbox_mode="workspace-read",
        stdin_open=False,
    )

    assert await process.wait() == 7
    assert await process.stdout.read() == b"early output"
    assert client._processes == {}
    assert client._early_events == {}
    assert client._early_event_count == 0
    assert client._early_event_bytes == 0

    client._handle_process_event(exit_event, generation=1)
    assert client._early_events == {}


@pytest.mark.parametrize("budget", ("processes", "events", "bytes"))
def test_sidecar_early_event_cache_is_bounded(tmp_path, budget: str) -> None:
    """验证未知进程事件不能突破任一早到缓存预算。"""
    client = _fake_startable_client(tmp_path)
    client.EARLY_PROCESS_LIMIT = 1
    client.EARLY_EVENT_LIMIT = 1
    client.EARLY_BYTES_LIMIT = 1
    first = client._decode_process_event({
        "event": "stdout",
        "process_id": "sandbox-first",
        "data": base64.b64encode(b"x").decode("ascii"),
    }, "stdout")
    client._handle_process_event(first, generation=1)
    process_id = (
        "sandbox-second" if budget == "processes" else "sandbox-first"
    )
    payload = b"xx" if budget == "bytes" else b"x"
    second = client._decode_process_event({
        "event": "stdout",
        "process_id": process_id,
        "data": base64.b64encode(payload).decode("ascii"),
    }, "stdout")
    if budget == "processes":
        client.EARLY_EVENT_LIMIT = 2
        client.EARLY_BYTES_LIMIT = 2
    elif budget == "events":
        client.EARLY_BYTES_LIMIT = 2
    else:
        client.EARLY_EVENT_LIMIT = 2

    with pytest.raises(SandboxProtocolError) as raised:
        client._handle_process_event(second, generation=1)

    assert raised.value.backend_code == "sidecar_early_event_limit"
    assert len(client._early_events) == 1
    assert client._early_event_count == 1
    assert client._early_event_bytes == 1


@pytest.mark.anyio
async def test_late_response_cannot_complete_restarted_generation_request(
    tmp_path,
) -> None:
    """验证旧 generation 的迟到响应不会串入新请求。"""
    client = _fake_startable_client(tmp_path)
    client._generation = 2
    process = _FakeSidecarProcess(_FakeSidecarReader(
        _jsonl_frame({"event": "ready", "protocol_version": 1}),
        _jsonl_frame({"id": "g1:r1", "ok": True, "result": {"old": True}}),
        _jsonl_frame({"id": "g2:r1", "ok": True, "result": {"new": True}}),
    ))
    _bind_reader_transport(client, process, generation=2)
    response = asyncio.get_running_loop().create_future()
    response.add_done_callback(client._consume_future_exception)
    client._pending["g2:r1"] = response
    client._pending_methods["g2:r1"] = "ping"

    await client._read_events(process, 2, bytearray())

    assert response.result()["result"] == {"new": True}
    assert client._generation == 2
    assert client._pending["g2:r1"] is response


def test_sidecar_generation_isolates_reused_process_ids(tmp_path) -> None:
    """验证重启后复用 process id 不会接收旧代次迟到事件。"""
    client = _fake_startable_client(tmp_path)
    client._sidecar_generation = 1
    old_process = SidecarProcess(
        client,
        "sandbox-1",
        generation=1,
    )
    client._processes[(1, "sandbox-1")] = old_process
    client._settle_generation(
        1,
        SandboxUnavailable("synthetic sidecar exit"),
        outcome_unknown=True,
    )
    assert old_process.execution_outcome_unknown is True

    client._sidecar_generation = 2
    client._generation = 2
    client._transport_failed = False
    new_process = SidecarProcess(
        client,
        "sandbox-1",
        generation=2,
    )
    client._processes[(2, "sandbox-1")] = new_process
    old_output = client._decode_process_event({
        "event": "stdout",
        "process_id": "sandbox-1",
        "data": base64.b64encode(b"old").decode("ascii"),
    }, "stdout")
    new_output = client._decode_process_event({
        "event": "stdout",
        "process_id": "sandbox-1",
        "data": base64.b64encode(b"new").decode("ascii"),
    }, "stdout")

    client._handle_process_event(old_output, generation=1)
    client._handle_process_event(new_output, generation=2)
    new_process.finish(0)

    async def read_output() -> bytes:
        return await new_process.stdout.read()

    assert asyncio.run(read_output()) == b"new"
    assert client._processes == {}


@pytest.mark.anyio
async def test_unexpected_eof_marks_process_and_effectful_request_unknown(
    tmp_path,
) -> None:
    """验证就绪后异常 EOF 将在途副作用标记为结果未知且不可重试。"""
    client = _fake_startable_client(tmp_path)
    process = _FakeSidecarProcess(_FakeSidecarReader(
        _jsonl_frame({"event": "ready", "protocol_version": 1}),
    ))
    _bind_reader_transport(client, process)
    logical_process = SidecarProcess(
        client,
        "sandbox-running",
        generation=1,
    )
    client._processes[(1, "sandbox-running")] = logical_process
    pending_spawn = asyncio.get_running_loop().create_future()
    pending_spawn.add_done_callback(client._consume_future_exception)
    client._pending["g1:r1"] = pending_spawn
    client._pending_methods["g1:r1"] = "spawn"

    await client._read_events(process, 1, bytearray())

    assert logical_process.returncode == -1
    assert logical_process.execution_outcome_unknown is True
    assert client._processes == {}
    failure = pending_spawn.exception()
    assert isinstance(failure, SandboxOutcomeUnknown)
    assert failure.code == "execution_outcome_unknown"
    assert failure.retryable is False
    assert client._generation == 1


@pytest.mark.anyio
async def test_close_continues_after_request_reader_and_process_failures(
    tmp_path,
    monkeypatch,
) -> None:
    """验证关闭竞态中的单步失败不会跳过其余清理。"""
    client = _fake_startable_client(tmp_path)
    process = _FakeSidecarProcess(
        _FakeSidecarReader(block_after_lines=True),
        stderr=_FakeSidecarReader(block_after_lines=True),
    )

    def fail_terminate() -> None:
        raise OSError("synthetic terminate failure")

    async def fail_wait() -> int:
        raise OSError("synthetic wait failure")

    def fail_kill() -> None:
        raise OSError("synthetic kill failure")

    process.terminate = fail_terminate
    process.wait = fail_wait
    process.kill = fail_kill
    client._sidecar = process
    client._sidecar_generation = 1
    client._generation = 1
    client._ready = asyncio.get_running_loop().create_future()
    client._ready.set_result(True)

    async def reader_fails_during_cancel() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("synthetic reader cancellation failure") from exc

    reader_task = asyncio.create_task(reader_fails_during_cancel())
    stderr_task = asyncio.create_task(asyncio.Event().wait())
    client._reader_task = reader_task
    client._stderr_task = stderr_task
    await asyncio.sleep(0)

    async def fail_close_request(
        method: str,
        params,
        *,
        generation: int | None = None,
    ):
        del method, params, generation
        raise SandboxProtocolError("synthetic close failure")

    monkeypatch.setattr(client, "_request", fail_close_request)

    await client.close()

    assert isinstance(reader_task.exception(), RuntimeError)
    assert stderr_task.cancelled()
    assert client._sidecar is None
    assert client._reader_task is None
    assert client._stderr_task is None
    assert client._processes == {}
    assert client._pending == {}


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ("shell_command", "exec_command"))
async def test_unknown_outcome_is_fail_closed_for_both_command_entries(
    tmp_path,
    monkeypatch,
    tool: str,
) -> None:
    """验证结果未知不会自动重试到宿主进程或完整权限执行。"""
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
    spawn_count = [0]

    async def uncertain_spawn(**_kwargs):
        spawn_count[0] += 1
        logical_process = SidecarProcess(
            client,
            f"sandbox-unknown-{spawn_count[0]}",
        )
        logical_process.finish(-1, outcome_unknown=True)
        return logical_process

    host_spawn = AsyncMock(
        side_effect=AssertionError("host process fallback is forbidden"),
    )
    monkeypatch.setattr(client, "spawn", uncertain_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", host_spawn)
    try:
        execute = getattr(coding, tool)
        result = await execute(
            command="Write-Output side-effect",
            sandbox_mode="workspace-read",
        )
    finally:
        await coding.close()

    assert result["ok"] is False
    assert result["data"]["reason"] == "execution_outcome_unknown"
    assert result["data"]["execution_outcome_unknown"] is True
    assert spawn_count[0] == 1
    host_spawn.assert_not_awaited()


@pytest.mark.anyio
async def test_sidecar_thousand_process_soak_keeps_resources_bounded(
    tmp_path,
    monkeypatch,
) -> None:
    """验证一千次短命令后表、句柄和内存保持有界。"""
    client = _fake_startable_client(tmp_path)
    client._sidecar_generation = 1
    client._generation = 1

    async def already_started() -> None:
        return None

    process_number = [0]

    async def spawn_response(
        method: str,
        params,
        *,
        generation: int | None = None,
    ) -> dict[str, str]:
        del params
        assert method == "spawn"
        assert generation == 1
        process_number[0] += 1
        return {"process_id": f"sandbox-soak-{process_number[0]}"}

    monkeypatch.setattr(client, "ensure_started", already_started)
    monkeypatch.setattr(client, "_request", spawn_response)
    handle_count_before = _process_handle_count()
    gc.collect()
    tracemalloc.start()
    memory_before, _ = tracemalloc.get_traced_memory()

    for _ in range(1000):
        logical_process = await client.spawn(
            argv=("short-command",),
            cwd=tmp_path,
            env={},
            sandbox_mode="workspace-read",
            stdin_open=False,
        )
        exit_event = client._decode_process_event({
            "event": "exit",
            "process_id": logical_process.process_id,
            "exit_code": 0,
        }, "exit")
        client._handle_process_event(exit_event, generation=1)
        assert await logical_process.wait() == 0

    gc.collect()
    memory_after, memory_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    handle_count_after = _process_handle_count()

    assert process_number[0] == 1000
    assert client._processes == {}
    assert client._pending == {}
    assert client._early_events == {}
    assert client._early_event_count == 0
    assert client._early_event_bytes == 0
    assert len(client._completed_processes) <= client.COMPLETED_PROCESS_LIMIT
    assert memory_after - memory_before < 512 * 1024
    assert memory_peak - memory_before < 2 * 1024 * 1024
    if handle_count_before is not None and handle_count_after is not None:
        assert handle_count_after - handle_count_before <= 4
