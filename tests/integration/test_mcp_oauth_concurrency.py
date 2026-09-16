# -*- coding: utf-8 -*-

import asyncio

import pytest
from pathlib import Path
from unittest.mock import Mock

from agent.application.mcp.oauth import McpOAuthService
from agent.domain.mcp_oauth import (
    McpOAuthLoginRequest,
    McpOAuthStorageError,
    McpOAuthTarget,
)
from agent.ports.mcp_oauth import McpOAuthPresenter
from infrastructure.mcp.oauth_adapter import McpOAuthAdapter
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore
from tests.fakes.mcp_credentials import MemoryVault
from tests.infrastructure.mcp.oauth_browser import OAuthBrowser
from tests.infrastructure.mcp.oauth_fixture import (
    OAuthService,
    RESOURCE_URL,
)


@pytest.mark.anyio
async def test_logout_rejects_late_login_commit_without_holding_lock_during_browser_wait(tmp_path: Path) -> None:
    remote = OAuthService()
    browser = OAuthBrowser(remote)
    browser_entered = asyncio.Event()
    resume_browser = asyncio.Event()
    vault = MemoryVault()
    store = SystemMcpCredentialStore(config_root=tmp_path / "config", state_root=tmp_path / "state", vault=vault)

    async def open_browser(url: str) -> bool:
        browser_entered.set()
        await resume_browser.wait()
        return await browser.open(url)

    service = McpOAuthService(store, McpOAuthAdapter(open_browser=open_browser, client_factory=remote.client))
    target = McpOAuthTarget("server", RESOURCE_URL)
    task = asyncio.create_task(service.login(McpOAuthLoginRequest(target), Mock(spec=McpOAuthPresenter)))
    try:
        await asyncio.wait_for(browser_entered.wait(), 3)
        await asyncio.wait_for(service.logout(target), 3)
        resume_browser.set()
        with pytest.raises(McpOAuthStorageError) as error:
            await task
        assert error.value.code == "credential_conflict"
        assert (await store.read(target)).snapshot is None
        assert not vault.values
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
