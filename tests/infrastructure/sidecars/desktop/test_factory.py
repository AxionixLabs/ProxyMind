from pathlib import Path
from unittest.mock import (
    AsyncMock,
    patch,
)

import pytest

from agent.ports.desktop import DesktopError
from infrastructure.sidecars.desktop.factory import DesktopSidecarFactory


@pytest.mark.anyio
async def test_platform_asset_selection_is_lazy(tmp_path: Path) -> None:
    executable = tmp_path / "version/desktop-sidecar.exe"
    with (patch("infrastructure.sidecars.desktop.factory.create_desktop_session", new_callable=AsyncMock) as start,
          patch("infrastructure.sidecars.desktop.factory.DesktopRuntimeInstaller.ensure", new_callable=AsyncMock,
                return_value=executable) as ensure):
        factory = DesktopSidecarFactory(tmp_path, platform="win32", architecture="x64")
        ensure.assert_not_called()
        start.assert_not_called()
        assert await factory() is start.return_value
        ensure.assert_awaited_once_with()
        start.assert_awaited_once_with(executable, platform="win32")


@pytest.mark.anyio
@pytest.mark.parametrize(("platform", "arch"), [("linux", "x64"), ("darwin", "x64"), ("win32", "arm64")])
async def test_unsupported_native_platform_never_downloads(tmp_path: Path, platform: str, arch: str) -> None:
    with pytest.raises(DesktopError) as failure:
        await DesktopSidecarFactory(tmp_path, platform=platform, architecture=arch)()
    assert failure.value.code == "unsupported_platform"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize("version", ["14.2", "15.7.2", "26"])
async def test_macos_arm64_selects_its_own_runtime(tmp_path: Path, version: str) -> None:
    executable = tmp_path / "DesktopSidecar.app/Contents/MacOS/desktop-sidecar"
    with (patch("infrastructure.sidecars.desktop.factory.create_desktop_session", new_callable=AsyncMock) as start,
          patch("infrastructure.sidecars.desktop.factory.DesktopRuntimeInstaller") as install):
        install.return_value.ensure = AsyncMock(return_value=executable)
        factory = DesktopSidecarFactory(tmp_path, platform="darwin", architecture="arm64", system_version=version)
        install.assert_not_called()
        await factory()
        install.assert_called_once_with(tmp_path, platform="darwin", progress=None)
        start.assert_awaited_once_with(executable, platform="darwin")


@pytest.mark.anyio
@pytest.mark.parametrize("version", ["", "13.7", "14.1.2", "unknown"])
async def test_macos_unsupported_version_never_downloads(tmp_path: Path, version: str) -> None:
    with patch("infrastructure.sidecars.desktop.factory.DesktopRuntimeInstaller") as install:
        with pytest.raises(DesktopError, match="macOS"):
            await DesktopSidecarFactory(tmp_path, platform="darwin", architecture="arm64", system_version=version)()
        install.assert_not_called()


@pytest.mark.anyio
async def test_failed_install_does_not_start_native_process(tmp_path: Path) -> None:
    with (patch("infrastructure.sidecars.desktop.factory.create_desktop_session", new_callable=AsyncMock) as start,
          patch("infrastructure.sidecars.desktop.factory.DesktopRuntimeInstaller.ensure", new_callable=AsyncMock,
                side_effect=ValueError("hash mismatch"))):
        with pytest.raises(DesktopError, match="hash mismatch"):
            await DesktopSidecarFactory(tmp_path, platform="win32", architecture="x64")()
        start.assert_not_called()
