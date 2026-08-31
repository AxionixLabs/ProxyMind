import asyncio

import pytest
from agent.capabilities import InMemoryProcessCapability
from infrastructure.platform.sandbox import (
    SandboxClient,
    _SidecarStream,
    sandbox_backend_name,
)
from infrastructure.platform.process_sessions import (
    ProcessSessionManager,
    ProcessSessionSpec,
)
from mind import create_native_coding
from infrastructure.config.paths import ApplicationLayout


def test_source_windows_sidecar_path_is_platform_specific(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MIND_SANDBOX_SERVER", raising=False)

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
        application_root=tmp_path,
        packaged=False,
        platform="win32",
    )

    assert resolved.executable == expected.resolve()
    assert resolved.available is True
    assert resolved.platform_name == "windows"


def test_packaged_macos_sidecar_path_is_separate(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MIND_SANDBOX_SERVER", raising=False)

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
        application_root=tmp_path,
        packaged=True,
        platform="darwin",
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
            application_root=tmp_path,
            packaged=False,
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
    asyncio.run(run("win32"))

    assert "level" not in captured["darwin"]
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
    coding = create_native_coding(
        root=tmp_path / "workspace",
        application_layout=layout,
    )

    try:
        sandbox_client = coding._process_sessions._sandbox_client
        assert sandbox_client is not None
        assert sandbox_client.application_root == layout.root
        assert sandbox_client.packaged is True
        assert sandbox_client.platform == layout.platform
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
