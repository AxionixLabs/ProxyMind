from mind_app.native_coding.exec.sandbox_client import (
    SandboxClient,
    sandbox_backend_name,
)


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
