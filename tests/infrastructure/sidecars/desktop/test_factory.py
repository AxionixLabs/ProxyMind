from pathlib import Path
from unittest.mock import (
    AsyncMock,
    patch,
)

import pytest

from agent.ports.desktop import DesktopError
from infrastructure.sidecars.desktop.factory import DesktopSidecarFactory


@pytest.mark.anyio
@pytest.mark.parametrize(("platform", "asset"), [
    ("win32", "cua_windows/publish/desktop-sidecar.exe"),
    ("darwin", "cua_macos/publish/desktop-sidecar"),
])
async def test_platform_asset_selection_is_lazy(tmp_path: Path, platform: str, asset: str) -> None:
    with patch("infrastructure.sidecars.desktop.factory.create_desktop_session", new_callable=AsyncMock) as start:
        factory = DesktopSidecarFactory(tmp_path, platform=platform)
        start.assert_not_called()
        assert await factory() is start.return_value
        start.assert_awaited_once_with(tmp_path / asset, platform=platform)


@pytest.mark.anyio
@pytest.mark.parametrize(("platform", "code"), [("linux", "unsupported_platform"), ("win32", "unavailable"), ("darwin", "unavailable")])
async def test_unsupported_or_missing_native_asset_fails_clearly(tmp_path: Path, platform: str, code: str) -> None:
    with pytest.raises(DesktopError) as failure:
        await DesktopSidecarFactory(tmp_path, platform=platform)()
    assert failure.value.code == code
