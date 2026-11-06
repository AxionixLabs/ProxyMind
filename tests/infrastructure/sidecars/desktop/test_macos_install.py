import hashlib
import io
import plistlib
import struct
import zipfile
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    patch,
)

import httpx
import pytest

from infrastructure.sidecars.assets import (
    MACOS_DESKTOP_EXECUTABLE,
    desktop_runtime_files,
)
from infrastructure.sidecars.desktop.install import DesktopRuntimeInstaller
from protocol.schema.runtime_manifest import RuntimeManifest


def macos_archive(*, cpu: int = 0x0100000C, minimum: int = 0x000E0200, identifier: str = "com.axionixlabs.desktop-sidecar") -> bytes:
    command = struct.pack("<6I", 0x32, 24, 1, minimum, 0x001A0000, 0)
    binary = struct.pack("<8I", 0xFEEDFACF, cpu, 0, 2, 1, len(command), 0, 0) + command
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(MACOS_DESKTOP_EXECUTABLE, binary)
        archive.writestr("DesktopSidecar.app/Contents/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": identifier, "CFBundleExecutable": "desktop-sidecar", "CFBundlePackageType": "APPL",
            "LSMinimumSystemVersion": "14.2", "LSUIElement": True,
        }))
        archive.writestr("DesktopSidecar.app/Contents/_CodeSignature/CodeResources", b"signature fixture")
    return output.getvalue()


def manifest_for(body: bytes) -> RuntimeManifest:
    return RuntimeManifest.model_validate({
        "host": "Mind", "service": "cua", "station": "darwin", "arch": "arm64", "channel": "stable",
        "version": "0.1.0", "build": "fixture", "published_at": "2026-11-06", "notes": "fixture",
        "package": {"filename": "cua-macos-arm64.zip", "size": len(body), "hash": hashlib.sha256(body).hexdigest(),
                    "updated_at": "2026-11-06", "url": "https://downloads.test/mac.zip"},
    })


@pytest.mark.anyio
async def test_mac_install_restores_entry_mode_and_revalidates_cached_signature(tmp_path: Path) -> None:
    body = macos_archive()
    client = httpx.AsyncClient
    with (patch("infrastructure.sidecars.desktop.install.fetch_manifest", new_callable=AsyncMock, return_value=manifest_for(body)) as fetch,
          patch("infrastructure.update.runtime.httpx.AsyncClient", side_effect=lambda **kwargs: client(
              transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)), **kwargs)),
          patch("infrastructure.sidecars.desktop.install._verify_macos_signature") as verify):
        executable = await DesktopRuntimeInstaller(tmp_path, platform="darwin").ensure()
        fetch.assert_awaited_once_with("cua", station="darwin", arch="arm64")
        assert executable == tmp_path / "macos-arm64" / f"0.1.0-{hashlib.sha256(body).hexdigest()[:16]}" / MACOS_DESKTOP_EXECUTABLE
        assert executable.stat().st_mode & 0o777 == 0o755
        runtime = executable.parents[3]
        assert len(desktop_runtime_files(runtime, platform="darwin")) == 3
        assert (runtime / "DesktopSidecar.app/Contents/Info.plist").stat().st_mode & 0o111 == 0
        fetch.return_value = None
        assert await DesktopRuntimeInstaller(tmp_path, platform="darwin").ensure() == executable
        assert verify.call_count == 2
        executable.chmod(0o644)
        with pytest.raises(ValueError, match="offline"):
            await DesktopRuntimeInstaller(tmp_path, platform="darwin").ensure()


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", ["architecture", "deployment", "identity", "signature"])
async def test_mac_invalid_bundle_never_publishes(tmp_path: Path, invalid: str) -> None:
    body = macos_archive(cpu=0x01000007 if invalid == "architecture" else 0x0100000C,
                         minimum=0x000F0000 if invalid == "deployment" else 0x000E0200,
                         identifier="changed.identity" if invalid == "identity" else "com.axionixlabs.desktop-sidecar")
    client = httpx.AsyncClient
    with (patch("infrastructure.sidecars.desktop.install.fetch_manifest", new_callable=AsyncMock, return_value=manifest_for(body)),
          patch("infrastructure.update.runtime.httpx.AsyncClient", side_effect=lambda **kwargs: client(
              transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)), **kwargs)),
          patch("infrastructure.sidecars.desktop.install._verify_macos_signature", side_effect=ValueError("invalid signature") if invalid == "signature" else None)):
        with pytest.raises(ValueError):
            await DesktopRuntimeInstaller(tmp_path, platform="darwin").ensure()
    assert not (tmp_path / "macos-arm64/current.json").exists()
    assert not list(tmp_path.rglob(".install-*"))
