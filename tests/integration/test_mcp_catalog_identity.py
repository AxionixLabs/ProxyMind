# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import (
    AsyncMock,
    patch,
)

import httpx
import pytest
from mcp.shared.exceptions import McpError

from agent.ports.mcp_runtime import McpRuntimeContext
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from tests.infrastructure.mcp.oauth_fixture import (
    ProbeRpcRequest,
    RESOURCE_URL,
)
from tests.infrastructure.mcp.oauth_runtime_fixture import RuntimeOAuthFixture


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["before_start", "during_discovery", "before_call", "unavailable", "at_send", "after_success"])
async def test_catalog_reuse_and_calls_are_bound_to_credential_generation(tmp_path, change):
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save()
    config = ConfigStore(tmp_path / "runtime.toml")
    config.update({("mcp_servers", fixture.target.config_key): {
        "url": RESOURCE_URL, "optional_startup_wait_sec": 0.02,
    }})
    runtime = ExternalMcpRuntime(McpRuntimeContext(
        ConfigSession(config, workspace=tmp_path), AsyncMock(), AsyncMock(), lambda operation: operation,
    ), credential_store=fixture.store)
    entered, release = asyncio.Event(), asyncio.Event()
    release.set()

    async def handle(request):
        if request.method == "POST":
            message = ProbeRpcRequest.model_validate_json(request.content)
            if message.method == "tools/list":
                entered.set()
                await release.wait()
        return await fixture.handle(request)

    async def ready():
        async with asyncio.timeout(3):
            while not runtime.started:
                await asyncio.sleep(0.01)

    with patch("httpx.AsyncHTTPTransport", side_effect=lambda **kwargs: httpx.MockTransport(handle)), patch(
        "infrastructure.mcp.external_group.preflight_server", AsyncMock(),
    ):
        try:
            await runtime.start()
            await ready()
            if change in ("before_call", "unavailable", "at_send", "after_success"):
                await runtime.stop_services()
                release.clear()
                entered.clear()
                await runtime.start()
                with runtime.use_tools() as tools:
                    assert tools is not None
                    assert tools.tools
                    await asyncio.wait_for(entered.wait(), 3)
                    release.set()
                    await ready()
                    if change == "after_success":
                        assert not (await tools.call_tool(next(iter(tools.tools)))).isError
                        await fixture.save()
                        with runtime.use_tools() as live:
                            assert not (await live.call_tool(next(iter(live.tools)))).isError
                        assert fixture.calls == 2
                        return
                    if change == "at_send":
                        original_view = fixture.store.view

                        async def view_then_replace(target):
                            value = await original_view(target)
                            await fixture.save()
                            return value

                        with patch.object(fixture.store, "view", side_effect=view_then_replace):
                            with pytest.raises(McpError):
                                await tools.call_tool(next(iter(tools.tools)))
                        assert fixture.calls == 0
                        return
                    if change == "before_call":
                        await fixture.save()
                    else:
                        fixture.vault.fail_read = True
                    with pytest.raises(McpError, match="authorization changed"):
                        await tools.call_tool(next(iter(tools.tools)))
                assert fixture.calls == 0
                return
            await runtime.stop_services()
            release.clear()
            entered.clear()
            if change == "before_start":
                await fixture.save()
            await runtime.start()
            await asyncio.wait_for(entered.wait(), 3)
            with runtime.use_tools() as tools:
                assert tools is not None
                if change == "before_start":
                    assert not tools.tools
                    release.set()
                    await ready()
                else:
                    assert tools.tools
                    await fixture.save()
                    release.set()
                    with pytest.raises(McpError, match="authorization changed|catalog changed"):
                        await tools.call_tool(next(iter(tools.tools)))
            assert fixture.calls == 0
        finally:
            release.set()
            fixture.vault.fail_read = False
            await runtime.stop()
