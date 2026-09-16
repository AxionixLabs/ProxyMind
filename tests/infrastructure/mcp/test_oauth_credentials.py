# -*- coding: utf-8 -*-

import asyncio
import dataclasses
import json
import sqlite3
import threading
import traceback

import anyio
import pytest
from pathlib import Path
from unittest.mock import patch

from agent.domain.mcp_oauth import (
    McpOAuthClientInfo,
    McpOAuthCredentialSnapshot,
    McpOAuthStorageError,
    McpOAuthTarget,
    McpOAuthToken,
)
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore
from tests.fakes.mcp_credentials import MemoryVault


def snapshot(target: McpOAuthTarget, generation: int = 0) -> McpOAuthCredentialSnapshot:
    return McpOAuthCredentialSnapshot(
        target=target,
        issuer="https://auth.example/issuer",
        resource="https://mcp.example/mcp",
        token_endpoint="https://auth.example/issuer/token",
        client=McpOAuthClientInfo("public-client", ("http://127.0.0.1:12345/callback",), "dynamic"),
        generation=generation,
        token=McpOAuthToken("access-secret", "refresh-secret", 2000.0, ("read", "write")),
    )


def store_at(root: Path, vault: MemoryVault, now: float = 1000.0) -> SystemMcpCredentialStore:
    return SystemMcpCredentialStore(config_root=root / "config", state_root=root / "state", vault=vault, clock=lambda: now)


@pytest.fixture
def target() -> McpOAuthTarget:
    return McpOAuthTarget(" sentry ", "https://mcp.example:443/mcp/app?tenant=a")


@pytest.mark.anyio
async def test_restore_absolute_expiration_and_safe_projection(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    assert (await store.view(target)).state == "missing"
    async with store.transaction(target) as transaction:
        saved = await transaction.save(snapshot(target))
    assert saved.generation == 1
    restarted = store_at(tmp_path, vault, now=1950)
    restored = (await restarted.read(target)).snapshot
    assert restored == saved
    assert restored is not None and restored.token is not None
    assert restored.token.remaining_lifetime(1950) == 50
    view = await restarted.view(target)
    assert view.state == "stored" and view.expires_at == 2000
    assert (await store_at(tmp_path, vault, now=2000).view(target)).state == "expired"
    assert restored.token.remaining_lifetime(2500) == 0
    for value in (repr(saved), repr(saved.token), repr(view), json.dumps(dataclasses.asdict(view))):
        assert "access-secret" not in value and "refresh-secret" not in value
        assert "public-client" not in value
    for path in (tmp_path / "state").rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"access-secret" not in data and b"refresh-secret" not in data
            assert b"mcp.example" not in data and b"public-client" not in data


@pytest.mark.anyio
async def test_registered_and_unknown_expiration_are_distinct(tmp_path: Path, target: McpOAuthTarget) -> None:
    store = store_at(tmp_path, MemoryVault())
    async with store.transaction(target) as transaction:
        registered = await transaction.save(dataclasses.replace(snapshot(target), token=None))
    assert (await store.view(target)).state == "registered"
    unknown = McpOAuthToken("access", None, None, None)
    async with store.transaction(target) as transaction:
        await transaction.save(dataclasses.replace(registered, token=unknown))
    assert (await store.view(target)).state == "stored"
    assert unknown.remaining_lifetime(99999) is None


@pytest.mark.anyio
async def test_target_and_namespace_isolation(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    async with store.transaction(target) as transaction:
        await transaction.save(snapshot(target))
    alternatives = (
        dataclasses.replace(target, config_key="sentry"),
        dataclasses.replace(target, server_url="https://mcp.example:444/mcp/app?tenant=a"),
        dataclasses.replace(target, server_url="https://mcp.example:443/mcp/other?tenant=a"),
        dataclasses.replace(target, server_url="https://mcp.example:443/mcp/app?tenant=b"),
    )
    for other in alternatives:
        assert (await store.read(other)).snapshot is None
    for isolated in (
        SystemMcpCredentialStore(config_root=tmp_path / "config2", state_root=tmp_path / "state", vault=vault),
        SystemMcpCredentialStore(config_root=tmp_path / "config", state_root=tmp_path / "state2", vault=vault),
    ):
        assert (await isolated.read(target)).snapshot is None
    saved = (await store.read(target)).snapshot
    assert saved is not None
    for binding in (
        {"issuer": "https://other.example", "resource": saved.resource, "client_id": saved.client.client_id},
        {"issuer": saved.issuer, "resource": saved.resource, "client_id": "another-client"},
        {"issuer": saved.issuer, "resource": "https://another.example/mcp", "client_id": saved.client.client_id},
    ):
        with pytest.raises(McpOAuthStorageError) as raised:
            saved.require_binding(**binding)
        assert raised.value.code == "credential_conflict"


@pytest.mark.anyio
async def test_partial_write_preserves_previous_and_cleans_orphan(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    async with store.transaction(target) as transaction:
        old = await transaction.save(snapshot(target))
    old_keys = set(vault.values)
    vault.fail_write = vault.writes + 2
    async with store.transaction(target) as transaction:
        with pytest.raises(McpOAuthStorageError) as raised:
            await transaction.save(dataclasses.replace(old, token=McpOAuthToken("long-secret" * 900, None, None, ())))
        assert raised.value.code == "storage_unavailable"
    assert old_keys < set(vault.values)
    assert (await store_at(tmp_path, vault).read(target)).snapshot == old
    assert set(vault.values) == old_keys


@pytest.mark.anyio
async def test_index_commit_failure_preserves_old_generation(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    async with store.transaction(target) as transaction:
        old = await transaction.save(snapshot(target))
    old_keys = set(vault.values)
    connect = sqlite3.connect

    def deny_state_update(action: int, table: str | None, column: str | None, database: str | None, source: str | None) -> int:
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE and table == "state" else sqlite3.SQLITE_OK

    def connection_with_commit_failure(path: Path) -> sqlite3.Connection:
        connection = connect(path)
        connection.set_authorizer(deny_state_update)
        return connection

    async with store.transaction(target) as transaction:
        with patch("infrastructure.mcp.oauth_credentials.sqlite3.connect", side_effect=connection_with_commit_failure):
            with pytest.raises(McpOAuthStorageError):
                await transaction.save(dataclasses.replace(old, token=None))
    assert old_keys < set(vault.values)
    assert (await store_at(tmp_path, vault).read(target)).snapshot == old
    assert set(vault.values) == old_keys


@pytest.mark.anyio
async def test_cleanup_failure_after_commit_restores_new_version(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    async with store.transaction(target) as transaction:
        old = await transaction.save(snapshot(target))
        vault.fail_delete = True
        with pytest.raises(McpOAuthStorageError):
            await transaction.save(dataclasses.replace(old, token=None))
    vault.fail_delete = False
    restored = (await store_at(tmp_path, vault).read(target)).snapshot
    assert restored is not None and restored.generation == 2 and restored.token is None


@pytest.mark.anyio
async def test_cancellation_during_save_waits_for_io_before_releasing_lock(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    writing = threading.Event()
    release = threading.Event()
    scopes: list[anyio.CancelScope] = []
    write_new = vault.write_new

    def blocked_write(key: str, value: str) -> None:
        writing.set()
        assert release.wait(5)
        write_new(key, value)

    async def save_then_cancel() -> None:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            async with store.transaction(target) as transaction:
                await transaction.save(snapshot(target))

    with patch.object(vault, "write_new", side_effect=blocked_write):
        async with anyio.create_task_group() as group:
            group.start_soon(save_then_cancel)
            assert await anyio.to_thread.run_sync(writing.wait, 5)
            scopes[0].cancel()
            try:
                competitor = SystemMcpCredentialStore(
                    config_root=tmp_path / "config", state_root=tmp_path / "state", vault=vault, lock_timeout=0.05,
                )
                with pytest.raises(McpOAuthStorageError) as raised:
                    await competitor.read(target)
                assert raised.value.code == "storage_busy"
            finally:
                release.set()
    assert (await store.read(target)).generation == 1


@pytest.mark.anyio
async def test_task_cancel_does_not_abandon_write_or_release_lock_early(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    writing = threading.Event()
    release = threading.Event()
    write_new = vault.write_new

    def blocked_write(key: str, value: str) -> None:
        writing.set()
        assert release.wait(5)
        write_new(key, value)

    async def save() -> None:
        async with store.transaction(target) as transaction:
            await transaction.save(snapshot(target))

    with patch.object(vault, "write_new", side_effect=blocked_write):
        task = asyncio.create_task(save())
        assert await anyio.to_thread.run_sync(writing.wait, 5)
        task.cancel()
        try:
            competitor = SystemMcpCredentialStore(
                config_root=tmp_path / "config", state_root=tmp_path / "state", vault=vault, lock_timeout=0.05,
            )
            with pytest.raises(McpOAuthStorageError) as raised:
                await competitor.read(target)
            assert raised.value.code == "storage_busy"
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert (await store.read(target)).generation == 1


@pytest.mark.anyio
async def test_large_credentials_are_chunked_and_old_chunks_removed(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    long_token = McpOAuthToken("x" * 9000, "y" * 8000, None, ())
    async with store.transaction(target) as transaction:
        saved = await transaction.save(dataclasses.replace(snapshot(target), token=long_token))
    assert len(vault.values) > 10
    assert max(len(value.encode("utf-16-le")) for value in vault.values.values()) <= 2000
    assert (await store_at(tmp_path, vault).read(target)).snapshot == saved
    async with store.transaction(target) as transaction:
        await transaction.save(dataclasses.replace(saved, token=None))
    assert len(vault.values) <= 2


@pytest.mark.anyio
async def test_task_cancel_during_lock_acquisition_closes_undelivered_connection(tmp_path: Path, target: McpOAuthTarget) -> None:
    store = store_at(tmp_path, MemoryVault())
    locked = threading.Event()
    release = threading.Event()
    try_lock = store._try_lock

    def blocked_lock(address: str) -> sqlite3.Connection | None:
        connection = try_lock(address)
        locked.set()
        assert release.wait(5)
        return connection

    with patch.object(store, "_try_lock", side_effect=blocked_lock):
        task = asyncio.create_task(store.read(target))
        try:
            assert await anyio.to_thread.run_sync(locked.wait, 5)
            task.cancel()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert (await store.read(target)).generation == 0


@pytest.mark.anyio
async def test_delete_failure_keeps_tombstone_and_cleanup_is_retryable(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    async with store.transaction(target) as transaction:
        old = await transaction.save(snapshot(target))
    vault.fail_delete = True
    with pytest.raises(McpOAuthStorageError):
        await store.delete(target)
    assert (await store.view(target)).state == "unavailable"
    vault.fail_delete = False
    restarted = store_at(tmp_path, vault)
    record = await restarted.read(target)
    assert record.snapshot is None and record.generation == 2
    assert not vault.values
    async with restarted.transaction(target) as transaction:
        with pytest.raises(McpOAuthStorageError) as raised:
            await transaction.save(old)
        assert raised.value.code == "credential_conflict"
    result = await restarted.delete(target)
    assert not result.removed and result.generation == 3


@pytest.mark.anyio
@pytest.mark.parametrize("damage", ["missing", "payload", "database", "unknown_field", "wrong_target"])
async def test_corrupt_storage_is_diagnostic_and_never_missing(tmp_path: Path, target: McpOAuthTarget, damage: str) -> None:
    import base64
    import hashlib

    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    async with store.transaction(target) as transaction:
        await transaction.save(snapshot(target))
    database = next((tmp_path / "state").rglob("*.db"))
    if damage == "database":
        database.write_bytes(b"invalid sqlite database")
    elif damage in ("unknown_field", "wrong_target"):
        payload = json.loads(base64.b64decode("".join(vault.values.values())))
        if damage == "unknown_field":
            payload["snapshot"]["client"]["client_secret"] = "client-secret-forbidden"
        else:
            payload["snapshot"]["target"]["config_key"] = "someone-else"
        encoded = json.dumps(payload).encode()
        data = base64.b64encode(encoded).decode("ascii")
        assert len(data) <= len(vault.values) * 1000
        for number, key in enumerate(vault.values):
            vault.values[key] = data[number * 1000:(number + 1) * 1000]
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE revisions SET digest=?", (hashlib.sha256(encoded).hexdigest(),))
    else:
        key = next(iter(vault.values))
        if damage == "missing":
            del vault.values[key]
        else:
            vault.values[key] = "credential-secret-corrupt"
    view = await store.view(target)
    assert view.state == "unavailable" and view.error == "storage_corrupt"
    with pytest.raises(McpOAuthStorageError) as raised:
        await store.read(target)
    assert "secret" not in "".join(traceback.format_exception(raised.value))
    if damage != "database":
        assert (await store.delete(target)).removed
        assert not vault.values


@pytest.mark.anyio
async def test_unavailable_backend_and_state_directory_do_not_fall_back(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    vault.fail_read = True
    store = store_at(tmp_path, vault)
    assert (await store.view(target)).error == "storage_unavailable"
    vault.fail_read = False
    with patch("infrastructure.mcp.oauth_credentials.sqlite3.connect", side_effect=PermissionError("secret-error")):
        assert (await store.view(target)).error == "storage_unavailable"
    assert not vault.values
    assert not (tmp_path / "config").exists()


@pytest.mark.anyio
async def test_late_login_cannot_revive_logout_and_closed_transaction_rejects_use(tmp_path: Path, target: McpOAuthTarget) -> None:
    store = store_at(tmp_path, MemoryVault())
    captured = await store.read(target)
    await store.delete(target)
    async with store.transaction(target) as transaction:
        with pytest.raises(McpOAuthStorageError) as raised:
            await transaction.save(snapshot(target, captured.generation))
        assert raised.value.code == "credential_conflict"
    with pytest.raises(McpOAuthStorageError):
        await transaction.save(snapshot(target, 1))


@pytest.mark.anyio
async def test_different_targets_can_commit_while_one_is_locked(tmp_path: Path, target: McpOAuthTarget) -> None:
    store = store_at(tmp_path, MemoryVault())
    other = dataclasses.replace(target, config_key="other")
    async with store.transaction(target):
        with anyio.fail_after(2):
            async with store.transaction(other) as transaction:
                await transaction.save(snapshot(other))


@pytest.mark.anyio
async def test_same_target_wait_is_cancellable_and_timeout_does_not_release_owner(tmp_path: Path, target: McpOAuthTarget) -> None:
    vault = MemoryVault()
    store = store_at(tmp_path, vault)
    competing = SystemMcpCredentialStore(config_root=tmp_path / "config", state_root=tmp_path / "state", vault=vault, lock_timeout=0.05)
    async with store.transaction(target) as owner:
        with pytest.raises(McpOAuthStorageError) as raised:
            await competing.read(target)
        assert raised.value.code == "storage_busy"
        with anyio.move_on_after(0.03) as scope:
            await store.read(target)
        assert scope.cancel_called
        await owner.save(snapshot(target))
    assert (await competing.read(target)).generation == 1
