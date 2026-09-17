# -*- coding: utf-8 -*-

import asyncio

import anyio
import httpx
import pytest
from dataclasses import replace
from pathlib import Path

from agent.domain.mcp_oauth import (
    McpOAuthError,
    McpOAuthStorageError,
    McpRegisteredClient,
)
from tests.infrastructure.mcp.oauth_fixture import RESOURCE_URL
from tests.infrastructure.mcp.oauth_runtime_fixture import RuntimeOAuthFixture


async def request(fixture: RuntimeOAuthFixture, auth=None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handle), auth=auth or fixture.auth(), follow_redirects=False) as client:
        return await client.post(RESOURCE_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


@pytest.mark.anyio
async def test_cold_start_refresh_and_multiple_connections_consume_rotation_once(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    before = await fixture.save(expired=True)
    responses = await asyncio.gather(*(request(fixture) for _ in range(5)))
    assert all(response.status_code == 200 for response in responses)
    assert len(fixture.refresh_requests) == 1
    saved = (await fixture.store.read(fixture.target)).snapshot
    assert saved is not None and saved.token is not None and saved.token.access_token == "access-2"
    assert saved.token.expires_at == fixture.now + 3600 and saved.generation == before.generation + 2
    assert (await request(fixture)).status_code == 200
    assert len(fixture.refresh_requests) == 1


@pytest.mark.anyio
async def test_external_login_replacement_and_logout_are_observed_at_request_boundary(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save()
    auth = fixture.auth()
    assert (await request(fixture, auth)).status_code == 200
    fixture.token = replace(fixture.token, access_token="replacement", refresh_token="replacement-refresh")
    fixture.access = "replacement"
    await fixture.save()
    assert (await request(fixture, auth)).status_code == 200
    assert fixture.requests[-1].headers["Authorization"] == "Bearer replacement"
    await fixture.store.delete(fixture.target)
    count = len(fixture.requests)
    with pytest.raises(McpOAuthError) as error:
        await request(fixture, auth)
    assert error.value.code == "login_required" and len(fixture.requests) == count


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["network", "invalid_grant", "scope", "redirect", "storage", "invalid_token"])
async def test_failed_refresh_never_reconsumes_old_token_after_restart(tmp_path: Path, failure: str) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save(expired=True)
    fixture.refresh_mode = failure
    if failure == "storage":
        # The marker is one vault record; fail the first write of the rotated credential.
        fixture.vault.fail_write = fixture.vault.writes + 2
    with pytest.raises((McpOAuthError, McpOAuthStorageError)):
        await request(fixture)
    assert len(fixture.refresh_requests) == 1
    fixture.vault.fail_write = None
    view = await fixture.store.view(fixture.target)
    assert view.state in ("refresh_uncertain", "reauthorization_required")
    with pytest.raises(McpOAuthError):
        await request(fixture)
    assert len(fixture.refresh_requests) == 1
    assert not fixture.rpc_methods


@pytest.mark.anyio
async def test_refresh_without_rotation_preserves_refresh_token_and_new_expiry(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save(expired=True)
    fixture.refresh_mode = "no_rotation"
    assert (await request(fixture)).status_code == 200
    saved = (await fixture.store.read(fixture.target)).snapshot
    assert saved is not None and saved.token is not None
    assert saved.token.refresh_token == "refresh-1" and saved.token.expires_at == fixture.now + 3600


@pytest.mark.anyio
async def test_direct_cancellation_after_refresh_send_waits_for_rotation_commit(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save(expired=True)
    fixture.refresh_release.clear()
    task = asyncio.create_task(request(fixture))
    await asyncio.wait_for(fixture.refresh_entered.wait(), 3)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    fixture.refresh_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = (await fixture.store.read(fixture.target)).snapshot
    assert saved is not None and saved.token is not None and saved.token.access_token == "access-2"
    assert not fixture.rpc_methods


@pytest.mark.anyio
async def test_anyio_scope_cancellation_commits_rotation_without_sending_business_request(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save(expired=True)
    fixture.refresh_release.clear()
    scopes: list[anyio.CancelScope] = []

    async def call() -> None:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await request(fixture)

    task = asyncio.create_task(call())
    await asyncio.wait_for(fixture.refresh_entered.wait(), 3)
    scopes[0].cancel()
    fixture.refresh_release.set()
    await asyncio.wait_for(task, 3)
    saved = (await fixture.store.read(fixture.target)).snapshot
    assert saved is not None and saved.token is not None and saved.token.access_token == "access-2"
    assert not fixture.rpc_methods


@pytest.mark.anyio
async def test_logout_waits_for_refresh_then_leaves_tombstone(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save(expired=True)
    fixture.refresh_release.clear()
    request_task = asyncio.create_task(request(fixture))
    await asyncio.wait_for(fixture.refresh_entered.wait(), 3)
    logout = asyncio.create_task(fixture.store.delete(fixture.target))
    await asyncio.sleep(0)
    assert not logout.done()
    fixture.refresh_release.set()
    await request_task
    await logout
    assert (await fixture.store.read(fixture.target)).snapshot is None and not fixture.vault.values
    with pytest.raises(McpOAuthError):
        await request(fixture)
    assert len(fixture.refresh_requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403, 307])
async def test_rejected_request_is_not_replayed_or_redirected(tmp_path: Path, status: int) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save()
    fixture.resource_status = status
    with pytest.raises(McpOAuthError):
        await request(fixture)
    assert len(fixture.requests) == 1 and not fixture.refresh_requests


@pytest.mark.anyio
async def test_anonymous_connection_does_not_require_system_vault_but_existing_secrets_do(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    fixture.vault.fail_read = True
    fixture.anonymous = True
    assert (await request(fixture)).status_code == 200
    assert "Authorization" not in fixture.requests[-1].headers
    fixture.vault.fail_read = False
    await fixture.save()
    fixture.vault.fail_read = True
    with pytest.raises(McpOAuthStorageError):
        await request(fixture)
    assert len(fixture.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["client", "no_refresh"])
async def test_changed_binding_or_missing_refresh_requires_explicit_login(tmp_path: Path, change: str) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    if change == "client":
        fixture.binding = replace(fixture.binding, registration=McpRegisteredClient("other-client"))
    else:
        fixture.token = replace(fixture.token, refresh_token=None)
    await fixture.save(expired=True)
    with pytest.raises(McpOAuthError):
        await request(fixture)
    assert not fixture.requests


@pytest.mark.anyio
async def test_stale_401_does_not_delete_external_login_replacement(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save()

    async def handle(request: httpx.Request) -> httpx.Response:
        fixture.token = replace(fixture.token, access_token="replacement", refresh_token="replacement-refresh")
        await fixture.save()
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), auth=fixture.auth()) as client:
        with pytest.raises(McpOAuthError):
            await client.get(RESOURCE_URL)
    saved = (await fixture.store.read(fixture.target)).snapshot
    assert saved is not None and saved.token is not None and saved.token.access_token == "replacement"
    status = fixture.authorizations[-1]
    assert status.generation == saved.generation and status.state == "oauth"
    assert status.verification == "unverified" and status.error is None


@pytest.mark.anyio
async def test_blocked_refresh_has_finite_deadline_and_persistent_recovery_state(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save(expired=True)
    fixture.refresh_release.clear()
    with pytest.raises(McpOAuthError) as error:
        await asyncio.wait_for(request(fixture), 7)
    assert error.value.code == "refresh_uncertain"
    assert (await fixture.store.view(fixture.target)).state == "refresh_uncertain"
    assert fixture.authorizations[-1].state == "reauthorization_required"
    assert fixture.authorizations[-1].credentials == "refresh_uncertain"


@pytest.mark.anyio
async def test_late_success_does_not_authenticate_a_newer_pending_request(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save()
    entered = [asyncio.Event(), asyncio.Event()]
    released = [asyncio.Event(), asyncio.Event()]
    calls: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        index = len(calls)
        calls.append(request)
        entered[index].set()
        await released[index].wait()
        return httpx.Response(200)

    tasks: list[asyncio.Task[httpx.Response]] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), auth=fixture.auth()) as client:
        try:
            tasks.append(asyncio.create_task(client.get(RESOURCE_URL)))
            await asyncio.wait_for(entered[0].wait(), 2)
            saved = await fixture.save()
            tasks.append(asyncio.create_task(client.get(RESOURCE_URL)))
            await asyncio.wait_for(entered[1].wait(), 2)
            released[0].set()
            await tasks[0]
            assert fixture.authorizations[-1].generation == saved.generation
            assert fixture.authorizations[-1].verification == "unverified"
            released[1].set()
            await tasks[1]
            assert fixture.authorizations[-1].verification == "accepted"
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_cancel_while_waiting_for_credential_lock_does_not_consume_refresh(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save(expired=True)
    async with fixture.store.transaction(fixture.target):
        task = asyncio.create_task(request(fixture))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    assert not fixture.refresh_requests
    assert (await fixture.store.view(fixture.target)).state == "expired"
