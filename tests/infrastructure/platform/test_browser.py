# -*- coding: utf-8 -*-

import asyncio

import pytest
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

from infrastructure.platform.browser import open_browser_url


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [False, True])
async def test_windows_browser_launch_or_manual_fallback(failure: bool) -> None:
    with patch("infrastructure.platform.browser.sys.platform", "win32"), patch(
        "infrastructure.platform.browser.os.startfile", create=True, side_effect=OSError if failure else None,
    ) as start:
        assert await open_browser_url("https://example.test/authorize") is not failure
        start.assert_called_once_with("https://example.test/authorize")


@pytest.mark.anyio
@pytest.mark.parametrize(("platform", "launcher"), [("darwin", "open"), ("linux", "xdg-open")])
async def test_posix_browser_uses_argument_vector(platform: str, launcher: str) -> None:
    process = Mock(returncode=0, wait=AsyncMock(return_value=0))
    with patch("infrastructure.platform.browser.sys.platform", platform), patch(
        "infrastructure.platform.browser.shutil.which", return_value=f"/usr/bin/{launcher}",
    ) as which, patch("infrastructure.platform.browser.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
        assert await open_browser_url("https://example.test/authorize?a=1&b=2")
        which.assert_called_once_with(launcher)
        assert spawn.call_args.args == (f"/usr/bin/{launcher}", "https://example.test/authorize?a=1&b=2")
        assert "shell" not in spawn.call_args.kwargs


@pytest.mark.anyio
async def test_missing_launcher_keeps_manual_path() -> None:
    with patch("infrastructure.platform.browser.sys.platform", "linux"), patch("infrastructure.platform.browser.shutil.which", return_value=None):
        assert not await open_browser_url("https://example.test/authorize")
    assert not await open_browser_url("file:///private")


@pytest.mark.anyio
async def test_cancel_reaps_browser_launcher() -> None:
    process = Mock(returncode=None, wait=AsyncMock(side_effect=[asyncio.CancelledError, 0]))
    with patch("infrastructure.platform.browser.sys.platform", "linux"), patch(
        "infrastructure.platform.browser.shutil.which", return_value="/usr/bin/xdg-open",
    ), patch("infrastructure.platform.browser.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with pytest.raises(asyncio.CancelledError):
            await open_browser_url("https://example.test/authorize")
    process.kill.assert_called_once_with()
    assert process.wait.await_count == 2
